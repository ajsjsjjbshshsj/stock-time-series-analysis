"""Native training adapters. Imported only by an owned worker child."""
import numpy as np
from tasks.artifacts import read_json,write_json


def _finite_metrics(values):
    return {key:(float(value) if np.isfinite(value) else None) for key,value in values.items()
            if isinstance(value,(int,float,np.integer,np.floating))}


def _proof(directory,values,predictions):
    write_json(directory/'reload.json',dict(values=np.asarray(values).tolist(),predictions=np.asarray(predictions).tolist()))


def train_operation(task,directory,source,emit):
    request=task['request'];algorithm=request['algorithm']
    emit('stage',stage='INPUT')
    if algorithm=='transformer':
        from .transformer import train_transformer
        native=train_transformer(task,directory,source,emit)
    elif algorithm=='xgboost':native=_xgboost(task,directory,source,emit)
    else:native=_lightgbm(task,directory,source,emit)
    native.update(algorithm=algorithm,ts_code=request['ts_code'],stock_codes=request['stock_codes'])
    write_json(directory/'native.json',native);verify_native(directory,algorithm)
    # Public metadata is intentionally independent of file paths/full bindings.
    info={key:native[key] for key in ('score_units','horizon','holding_sessions','feature_names','feature_count',
        'train_start','train_end','seen_through','pool_count','metrics','probe_selected','epochs_completed','rounds','device','peak_vram_bytes','label_definition') if key in native}
    info['parameters']={k:request[k] for k in ('n_estimators','max_depth','num_epochs','batch_size','label','early_metric','patience','use_probe_selection')}
    return info


def _xgboost(task,directory,source,emit):
    from analysis.predictor import StockPredictor
    from analysis.strategy_samples import dated_samples,purged_splits
    import pandas as pd
    request=task['request'];frame,calendar,contract=source.load(task)
    from .source import history_fingerprint
    dependency=frame.attrs.get('feature_store_source_history')
    raw=dependency.to_frame() if dependency is not None else frame
    write_json(directory/'history.json',dict(cutoff=request['end_date'],prefixes={request['ts_code']:history_fingerprint(raw,request['end_date'])}))
    names=contract['feature_names'];emit('stage',stage='TRAIN')
    result=StockPredictor('xgboost').train_dated(frame,feature_names=names,calendar=calendar,model_params=dict(
        n_estimators=request['n_estimators'],max_depth=request['max_depth'],n_jobs=1,random_state=42,tree_method='hist',device='cpu'))
    model=result['model'];model.save_model(directory/'model.json')
    groups=purged_splits(dated_samples(frame,names,horizon=1,classification=True))
    values=groups['val'][names].head(16);_proof(directory,values,model.predict_proba(values)[:,1])
    return dict(feature_names=names,feature_count=len(names),score_units='probability_up_1d',horizon=1,holding_sessions=1,
        train_start=result['metadata']['splits']['train']['signal_start'],train_end=result['metadata']['splits']['train']['signal_end'],
        seen_through=result['metadata']['seen_through'],pool_count=1,metrics=_finite_metrics(result['metrics']),
        params=result['metadata']['engineering_params'],splits=result['metadata']['splits'],probe_selected=False)


def _lightgbm(task,directory,source,emit):
    import lightgbm as lgb
    from tasks.disk_matrix import build_market_matrix,DiskFeatureSequence
    from tasks.market_profile import bounded_profile,parameters
    from analysis.disk_probe import disk_probe
    from analysis.market_validation import within_day_ranks,_DailyRankIC,ranking_metrics
    from .source import native_request
    r=task['request'];original=dict(id=task['id'],request=native_request(r),binding=task['binding']['data'])
    work=source.matrix_directory(task) if hasattr(source,'matrix_directory') else directory/'matrix'
    from .source import history_fingerprint
    prefixes={}
    def captured():
        for code,snapshot,calendar,proof in source.iter_stocks(task):
            prefixes[code]=history_fingerprint(getattr(snapshot,'source_history',snapshot.raw_prices),r['end_date'])
            yield code,snapshot,calendar,proof
    bundle=build_market_matrix(original,captured(),work,emit)
    write_json(directory/'history.json',dict(cutoff=r['end_date'],prefixes=prefixes))
    emit('stage',stage='PROBE');names,probe=disk_probe(bundle,bundle.feature_names,bounded_profile(),emit)
    write_json(directory/'probe_selection.json',probe)
    train_index=bundle.splits['train'];val_index=bundle.splits['val'];columns=[bundle.feature_names.index(n) for n in names]
    ytrain=np.asarray(bundle.labels[train_index]);yval=np.asarray(bundle.labels[val_index])
    params=parameters(bounded_profile(),'regression');params['metric']='None' if r['early_metric']=='rank_ic' else 'rmse'
    metric=_DailyRankIC(yval,bundle.dates[val_index])
    def feval(pred,data):
        value=metric.evaluate(pred)['mean_daily_rank_ic']
        return 'mean_daily_rank_ic',-1. if value is None else value,True
    def progress(env):
        if (env.iteration+1)%10==0:emit('progress',phase='TRAIN',completed=env.iteration+1,total=r['n_estimators'])
    transform=lambda y,index:within_day_ranks(y,bundle.dates[index]) if r['label']=='daily_rank' else y
    ds=lgb.Dataset(DiskFeatureSequence(bundle.features,train_index,columns),label=transform(ytrain,train_index),feature_name=names,params=params)
    vs=lgb.Dataset(DiskFeatureSequence(bundle.features,val_index,columns),label=transform(yval,val_index),feature_name=names,reference=ds,params=params)
    emit('stage',stage='TRAIN')
    model=lgb.train(params,ds,num_boost_round=r['n_estimators'],valid_sets=[vs],
        feval=feval if r['early_metric']=='rank_ic' else None,
        callbacks=[progress,lgb.early_stopping(r['patience'],verbose=False),lgb.log_evaluation(0)])
    sequence=DiskFeatureSequence(bundle.features,val_index,columns);pred=np.empty(len(sequence))
    for start in range(0,len(sequence),4096):pred[start:start+4096]=model.predict(sequence[start:start+4096],num_threads=2)
    metrics=ranking_metrics(pred,yval,bundle.dates[val_index]);metrics.pop('daily')
    if r['label']=='daily_rank':metrics['rmse']=None
    model.save_model(str(directory/'model.txt'));values=sequence[:min(16,len(sequence))]
    _proof(directory,values,model.predict(values,num_threads=2))
    return dict(feature_names=names,feature_count=len(names),score_units='within_day_centered_rank' if r['label']=='daily_rank' else 'raw_return_5d',
        horizon=5,holding_sessions=5,train_start=bundle.split_metadata['train']['signal_start'],train_end=bundle.split_metadata['train']['signal_end'],
        seen_through=bundle.split_metadata['val']['label_end'],pool_count=task['binding']['data']['pool_count'],
        metrics=metrics,params=params,splits=bundle.split_metadata,probe_selected=True,rounds=model.current_iteration())


def verify_native(directory,algorithm):
    native=read_json(directory/'native.json',max_bytes=8*1024*1024)
    if native['algorithm']!=algorithm:raise ValueError('Native algorithm mismatch')
    if algorithm=='transformer':
        from .transformer import verify_transformer
        return verify_transformer(directory,native)
    proof=read_json(directory/'reload.json');values=np.asarray(proof['values'],dtype=float)
    expected=np.asarray(proof['predictions'],dtype=float);names=native['feature_names']
    if values.ndim!=2 or not 1<=len(values)<=16 or values.shape[1]!=len(names) or not np.isfinite(values).all():
        raise ValueError('Invalid native proof')
    if algorithm=='xgboost':
        import pandas as pd
        from xgboost import XGBClassifier
        model=XGBClassifier();model.load_model(directory/'model.json')
        if model.get_booster().feature_names!=names:raise ValueError('XGBoost feature order changed')
        actual=model.predict_proba(pd.DataFrame(values,columns=names))[:,1]
    else:
        import lightgbm as lgb
        model=lgb.Booster(model_file=str(directory/'model.txt'))
        if model.feature_name()!=names:raise ValueError('LightGBM feature order changed')
        actual=model.predict(values,num_threads=2)
    if expected.shape!=actual.shape or not np.isfinite(expected).all() or not np.allclose(actual,expected,rtol=1e-6,atol=1e-7):
        raise ValueError('Native reload failed')
    return model
