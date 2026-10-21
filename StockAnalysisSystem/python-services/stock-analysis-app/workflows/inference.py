"""Chunked frozen-model scoring; unknown labels never hide latest signals."""
import numpy as np
import pandas as pd
from tasks.artifacts import write_json
from .training import verify_native


def iter_predictions(task,source):
    r=task['request'];_,metadata=source.model(r['model_id']);native=metadata['native']
    folder=source.store.path(r['model_id']);model=verify_native(folder,r['algorithm']);names=native['feature_names']
    if r['algorithm']=='transformer':
        from .transformer import transformer_predictions
        yield from transformer_predictions(task,source,model,native)
    elif r['algorithm']=='xgboost':
        frame,_,_=source.load(task);frame=frame.copy();frame['trade_date']=pd.to_datetime(frame.trade_date);prices=frame
        frame=frame[frame.trade_date.between(pd.Timestamp(r['start_date']),pd.Timestamp(r['end_date']))]
        valid=np.isfinite(frame[names].to_numpy(dtype=float)).all(axis=1);eligible=frame.loc[valid]
        scores=eligible[['ts_code','trade_date']].copy()
        scores['score']=model.predict_proba(eligible[names])[:,1] if len(eligible) else np.empty(0)
        scores['predicted_class']=(scores.score>=.5).astype(int)
        scores['trade_date']=scores.trade_date.dt.strftime('%Y-%m-%d')
        yield scores,prices,dict(excluded_rows=int((~valid).sum()))
    else:
        from tasks.market_samples import qualify_signals
        from .source import native_request
        for code,snapshot,calendar,proof in source.iter_stocks(task):
            from dataclasses import is_dataclass,replace
            from copy import copy
            clipped={name:getattr(snapshot,name)[pd.to_datetime(getattr(snapshot,name).trade_date).between(
                pd.Timestamp(r['start_date']),pd.Timestamp(r['end_date']))].copy() for name in ('frame','raw_prices')}
            if is_dataclass(snapshot):signal_source=replace(snapshot,**clipped)
            else:
                signal_source=copy(snapshot)
                for name,value in clipped.items():setattr(signal_source,name,value)
            eligible,coverage=qualify_signals(signal_source,calendar,proof,native_request(r))
            scores=eligible[['ts_code','trade_date']].copy()
            values=eligible[names].to_numpy(dtype='<f4').astype(np.float64)
            scores['score']=model.predict(values,num_threads=2) if len(values) else np.empty(0)
            scores['trade_date']=pd.to_datetime(scores.trade_date).dt.strftime('%Y-%m-%d')
            yield scores,snapshot.raw_prices,coverage


def collect_predictions(task,directory,source,emit,*,keep_prices=False):
    (directory/'scores').mkdir(exist_ok=False);parts=[];count=0;stocks=set();dates=set();exclusions=[];all_scores=[];prices=[];files=0
    for i,(scores,raw,coverage) in enumerate(iter_predictions(task,source),1):
        if scores.duplicated(['ts_code','trade_date']).any() or not np.isfinite(scores.score.to_numpy(dtype=float)).all():
            raise ValueError('Invalid model score identities/values')
        parts.append(scores);count+=len(scores);stocks.update(scores.ts_code);dates.update(scores.trade_date)
        if coverage.get('reasons') or coverage.get('excluded_rows'):exclusions.append(coverage)
        if keep_prices:all_scores.append(scores);prices.append(raw)
        if i%100==0:
            pd.concat(parts,ignore_index=True).to_parquet(directory/'scores'/f'{files:05d}.parquet',index=False);files+=1;parts=[]
            emit('progress',phase='INPUT',completed=i,total=task['binding'].get('pool_count',i))
    if parts:pd.concat(parts,ignore_index=True).to_parquet(directory/'scores'/f'{files:05d}.parquet',index=False)
    if not count:raise ValueError('No eligible signals in requested period')
    write_json(directory/'eligibility.json',dict(stocks_with_signals=len(stocks),score_rows=count,dates=sorted(dates),exclusions=exclusions))
    return dict(score_rows=count,scored_stocks=len(stocks),signal_start=min(dates),signal_end=max(dates)),all_scores,prices


def predict_operation(task,directory,source,emit):
    emit('stage',stage='INPUT');info,_,_=collect_predictions(task,directory,source,emit)
    row,meta=source.model(task['request']['model_id']);native=meta['native']
    r=task['request']
    report=dict(info,status='COMPLETE',score_units=native['score_units'],horizon=native['horizon'],
        requested_start=r['start_date'],requested_end=r['end_date'],data_cutoff=task['binding'].get('data',{}).get('source_cutoff',r['end_date']),
        holding_sessions=native['holding_sessions'],model_manifest_sha256=row['manifest_sha256'],seen_through=native['seen_through'],
        sample_relation='historical_research' if info['signal_start']<=native['seen_through'] else 'post_model_selection',
        warning='Scores are model outputs, not guaranteed prices, returns or executable orders',published=False)
    write_json(directory/'report.json',report);return report
