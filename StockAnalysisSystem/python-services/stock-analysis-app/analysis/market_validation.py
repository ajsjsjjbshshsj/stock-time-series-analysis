"""Fixed train/validation-only experiments, separate from engineering releases."""
import numpy as np
import pandas as pd


def trial_plan():
    """Pre-registered comparisons, not an open-ended parameter search."""
    return [dict(name=name,label=label,early_metric=metric,rounds=rounds,patience=patience)
        for name,label,metric,rounds,patience in (
            ('raw_rmse20','raw','rmse',20,10),
            ('raw_rmse300','raw','rmse',300,30),
            ('raw_rankic300','raw','rank_ic',300,30),
            ('csrank_rankic300','daily_rank','rank_ic',300,30))]


def nested_validation(bundle):
    """Whole-date early/comparison split; target dates define purge boundaries.

    Test start is identity metadata only. Test labels/features are never indexed.
    """
    selected={}
    for phase in ('train','val'):
        index=np.asarray(bundle.splits[phase])
        if (index.ndim!=1 or index.dtype.kind not in 'iu' or not len(index)
                or len(np.unique(index))!=len(index) or np.any(index<0) or np.any(index>=bundle.rows)):
            raise ValueError('Invalid experiment index')
        dates=np.asarray(bundle.dates[index]);targets=np.asarray(bundle.target_dates[index]);codes=np.asarray(bundle.codes[index])
        if (any(v.dtype.kind not in 'iu' for v in (dates,targets,codes)) or np.any(targets<=dates) or np.any(codes<0)
                or not np.array_equal(np.lexsort((codes,dates)),np.arange(len(index)))
                or np.any((dates[1:]==dates[:-1])&(codes[1:]==codes[:-1]))):
            raise ValueError('Invalid experiment dates/order')
        selected[phase]=index
    train,val=selected['train'],selected['val']
    if np.intersect1d(train,val).size:raise ValueError('Overlapping outer partitions')
    dates=bundle.dates[val];first=int(dates.min())
    test_start=int(np.datetime64(bundle.split_metadata['test']['signal_start'],'D').astype(np.int64))
    if (np.any(bundle.target_dates[train]>=first) or np.any(bundle.dates[train]>=first)
            or np.any(bundle.target_dates[val]>=test_start) or np.any(dates>=test_start)):
        raise ValueError('Outer label boundary crossed')
    unique=np.unique(dates);cut=int(len(unique)*.6)
    if not 0<cut<len(unique):raise ValueError('Insufficient validation dates')
    boundary=unique[cut]
    early=val[(dates<boundary)&(bundle.target_dates[val]<boundary)]
    compare=val[dates>=boundary]
    if not len(early) or not len(compare):raise ValueError('Empty nested partition')
    return dict(train=train,early=early,compare=compare)


def _vectors(values,dates):
    values=np.asarray(values,dtype=float);dates=np.asarray(dates)
    if (values.ndim!=1 or dates.ndim!=1 or not len(values) or values.shape!=dates.shape
            or dates.dtype.kind not in 'iu' or not np.isfinite(values).all()):
        raise ValueError('Invalid ranking vectors')
    return values,dates


class _DailyRankIC:
    """Cache label ranks; each callback only ranks new predictions per day."""
    def __init__(self,labels,dates):
        self.labels,self.dates=_vectors(labels,dates)
        order=np.argsort(self.dates,kind='stable');ordered=self.dates[order]
        edges=np.r_[0,np.flatnonzero(np.diff(ordered))+1,len(order)]
        self.groups=[]
        for a,b in zip(edges[:-1],edges[1:]):
            index=order[a:b];rank=pd.Series(self.labels[index]).rank(method='average').to_numpy()
            rank-=rank.mean();norm=np.linalg.norm(rank)
            self.groups.append((int(ordered[a]),index,rank/norm if norm>0 else None))

    def evaluate(self,predictions):
        pred,_=_vectors(predictions,self.dates);daily=[];values=[]
        for day,index,target in self.groups:
            rank=pd.Series(pred[index]).rank(method='average').to_numpy();rank-=rank.mean()
            norm=np.linalg.norm(rank);ic=None
            if target is not None and norm>0:
                ic=float(np.clip(np.dot(rank/norm,target),-1.,1.));values.append(ic)
            daily.append(dict(date=str(np.datetime64(day,'D')),rows=len(index),rank_ic=ic))
        return dict(mean_daily_rank_ic=float(np.mean(values)) if values else None,
            defined_dates=len(values),total_dates=len(daily),
            positive_daily_fraction=float((np.asarray(values)>0).mean()) if values else None,daily=daily)


def ranking_metrics(predictions,raw_labels,dates):
    labels,dates=_vectors(raw_labels,dates);pred,_=_vectors(predictions,dates)
    result=_DailyRankIC(labels,dates).evaluate(pred)
    result['rmse']=float(np.sqrt(np.mean((pred-labels)**2)))
    return result


def within_day_ranks(labels,dates):
    labels,dates=_vectors(labels,dates)
    frame=pd.DataFrame(dict(label=labels,date=dates));group=frame.groupby('date').label
    return (group.rank(method='average').to_numpy()-.5)/group.transform('size').to_numpy()-.5


def _parameters(trial):
    from tasks.market_profile import parameters,bounded_profile
    params=parameters(bounded_profile(),'regression')
    params['metric']='None' if trial['early_metric']=='rank_ic' else 'rmse'
    return params


def _selection(results):
    defined=[r for r in results if r['metrics']['mean_daily_rank_ic'] is not None]
    if not defined:return None,False
    best=sorted(defined,key=lambda r:(-r['metrics']['mean_daily_rank_ic'],r['name']))[0]
    baseline=results[0]['metrics']['mean_daily_rank_ic'];metric=best['metrics']
    qualified=(best['name']!=results[0]['name'] and metric['mean_daily_rank_ic']>0
        and (baseline is None or metric['mean_daily_rank_ic']>baseline)
        and metric['defined_dates']==metric['total_dates'])
    return best['name'],bool(qualified)


def run_trials(bundle,names,directory,guard,emit):
    """Sequential native fits; ONLY early-prefix data drive early stopping."""
    import gc,hashlib
    from pathlib import Path
    import lightgbm as lgb
    from tasks.artifacts import write_json,_is_link
    from tasks.disk_matrix import DiskFeatureSequence
    if not names or names!=[n for n in bundle.feature_names if n in names]:
        raise ValueError('Invalid experiment feature order')
    directory=Path(directory).absolute()
    if any(_is_link(p) for p in (directory,*directory.parents)):raise ValueError('Unsafe experiment directory')
    parts=nested_validation(bundle);guard();directory.mkdir(parents=True,exist_ok=False)
    columns=[bundle.feature_names.index(n) for n in names]
    phase_info={}
    for phase,index in parts.items():
        encoded=index.astype('<i8').tobytes()
        with (directory/(phase+'.i64')).open('xb') as stream:stream.write(encoded)
        phase_info[phase]=dict(rows=len(index),index_sha256=hashlib.sha256(encoded).hexdigest(),
            signal_start=str(np.datetime64(int(bundle.dates[index].min()),'D')),
            signal_end=str(np.datetime64(int(bundle.dates[index].max()),'D')),
            label_end=str(np.datetime64(int(bundle.target_dates[index].max()),'D')))
    write_json(directory/'preregistration.json',dict(schema_version=1,trials=trial_plan(),feature_names=names,
        partitions=phase_info,test_evaluated=False,published=False,
        selection='largest finite comparison mean daily RankIC, name tie-break; qualification requires positive improvement over raw_rmse20 and all comparison dates defined'))
    labels={phase:np.asarray(bundle.labels[index],dtype=float) for phase,index in parts.items()}
    if any(not np.isfinite(y).all() for y in labels.values()):raise ValueError('Nonfinite research labels')
    early_ic=_DailyRankIC(labels['early'],bundle.dates[parts['early']]);results=[]
    def sequence(phase):return DiskFeatureSequence(bundle.features,parts[phase],columns)
    for trial in trial_plan():
        guard();emit(stage='TRIAL_START',trial=trial['name'])
        target=directory/trial['name'];target.mkdir(exist_ok=False);params=_parameters(trial)
        y={phase:(within_day_ranks(labels[phase],bundle.dates[parts[phase]]) if trial['label']=='daily_rank' else labels[phase])
           for phase in ('train','early')}
        train=lgb.Dataset(sequence('train'),label=y['train'],feature_name=names,params=params)
        early=lgb.Dataset(sequence('early'),label=y['early'],reference=train,feature_name=names,params=params)
        def feval(pred,data):
            metric=early_ic.evaluate(pred)['mean_daily_rank_ic']
            # Constant scores are undefined, not evidence of neutral skill.
            return 'mean_daily_rank_ic',-1. if metric is None else metric,True
        def progress(environment):
            guard()
            if (environment.iteration+1)%10==0:
                emit(stage='TRAIN',trial=trial['name'],iteration=environment.iteration+1,total=trial['rounds'])
        progress.order=10
        model=lgb.train(params,train,num_boost_round=trial['rounds'],valid_sets=[early],
            feval=feval if trial['early_metric']=='rank_ic' else None,
            callbacks=[progress,lgb.early_stopping(trial['patience'],verbose=False,first_metric_only=True),lgb.log_evaluation(0)])
        compare=sequence('compare');pred=np.empty(len(compare),dtype=float)
        for start in range(0,len(compare),4096):
            guard();end=min(start+4096,len(compare))
            pred[start:end]=model.predict(compare[start:end],num_threads=2)
        metrics=ranking_metrics(pred,labels['compare'],bundle.dates[parts['compare']])
        if trial['label']=='daily_rank':metrics['rmse']=None
        result=dict(name=trial['name'],config=trial,params=params,rounds=model.current_iteration(),metrics=metrics,
            score_units='five-session raw return' if trial['label']=='raw' else 'within-day centered label rank')
        model.save_model(str(target/'model.txt'))
        values=compare[:min(16,len(compare))];expected=model.predict(values,num_threads=2)
        reload=lgb.Booster(model_file=str(target/'model.txt'))
        if reload.feature_name()!=names or not np.allclose(reload.predict(values,num_threads=2),expected,rtol=1e-12,atol=1e-12):
            raise ValueError('Research native reload mismatch')
        write_json(target/'reload.json',dict(values=values.tolist(),predictions=expected.tolist(),partition='compare'))
        write_json(target/'metadata.json',result)
        pd.DataFrame(dict(row_index=parts['compare'],stock_id=bundle.codes[parts['compare']],
            trade_date=bundle.dates[parts['compare']].astype('datetime64[D]'),raw_label=labels['compare'],score=pred)).to_parquet(target/'comparison.parquet',index=False)
        results.append(result);emit(stage='TRIAL_DONE',trial=trial['name'],rounds=result['rounds'],
            mean_daily_rank_ic=metrics['mean_daily_rank_ic'],defined_dates=metrics['defined_dates'])
        del model,reload,train,early,compare,y;gc.collect();guard()
    best,qualified=_selection(results)
    return dict(status='VALIDATION_ONLY_COMPLETE',purpose='validation_only_research',test_evaluated=False,
        published=False,trials=results,best_trial=best,qualified=qualified)


def _inventory(directory):
    from tasks.artifacts import _is_link,file_digest
    if any(_is_link(p) for p in (directory,*directory.parents)):raise ValueError('Unsafe experiment path')
    files={}
    for path in sorted(directory.rglob('*')):
        if _is_link(path):raise ValueError('Unsafe experiment file')
        name=path.relative_to(directory).as_posix()
        if path.is_file() and name not in ('manifest.json','complete.json'):files[name]=file_digest(path)
    return files


def seal_validation(directory,report):
    from pathlib import Path
    from tasks.artifacts import write_json,file_digest
    directory=Path(directory).absolute()
    if (directory/'manifest.json').exists() or (directory/'complete.json').exists():raise FileExistsError('Existing research seal')
    write_json(directory/'report.json',report)
    write_json(directory/'manifest.json',dict(schema_version=1,purpose='validation_only_research',files=_inventory(directory)))
    write_json(directory/'complete.json',dict(manifest_sha256=file_digest(directory/'manifest.json')))


def verify_validation(directory):
    """Research artifact verifier; never a released/engineering candidate."""
    from pathlib import Path
    import lightgbm as lgb
    from tasks.artifacts import read_json,file_digest
    directory=Path(directory).absolute();manifest=read_json(directory/'manifest.json')
    if (manifest!=dict(schema_version=1,purpose='validation_only_research',files=_inventory(directory))
            or read_json(directory/'complete.json')!=dict(manifest_sha256=file_digest(directory/'manifest.json'))):
        raise ValueError('Research manifest changed')
    report=read_json(directory/'report.json');protocol=read_json(directory/'preregistration.json')
    if (report.get('status')!='VALIDATION_ONLY_COMPLETE' or report.get('purpose')!='validation_only_research'
            or report.get('published') is not False or report.get('test_evaluated') is not False
            or 'test_metrics' in report or protocol.get('trials')!=trial_plan()
            or protocol.get('test_evaluated') is not False or protocol.get('published') is not False
            or len(report.get('trials',[]))!=4):raise ValueError('Invalid validation-only report')
    for result,trial in zip(report['trials'],trial_plan()):
        target=directory/trial['name'];probe=read_json(target/'reload.json')
        if (result.get('name')!=trial['name'] or result.get('config')!=trial or result.get('params')!=_parameters(trial)
                or type(result.get('rounds')) is not int or not 1<=result['rounds']<=trial['rounds']
                or read_json(target/'metadata.json')!=result or probe.get('partition')!='compare'):
            raise ValueError('Trial protocol mismatch')
        values=np.asarray(probe['values'],dtype=float);expected=np.asarray(probe['predictions'],dtype=float)
        if (values.ndim!=2 or not 1<=len(values)<=16 or values.shape[1]!=len(protocol['feature_names'])
                or expected.shape!=(len(values),) or not np.isfinite(values).all() or not np.isfinite(expected).all()):
            raise ValueError('Invalid research reload proof')
        model=lgb.Booster(model_file=str(target/'model.txt'))
        if (model.feature_name()!=protocol['feature_names'] or model.current_iteration()!=result['rounds']
                or not np.allclose(model.predict(values,num_threads=2),expected,rtol=1e-12,atol=1e-12)):
            raise ValueError('Research model changed')
    if (report['best_trial'],report['qualified'])!=_selection(report['trials']):raise ValueError('Research selection mismatch')
    return report
