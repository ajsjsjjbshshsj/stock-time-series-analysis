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
