"""Disk-backed model input; only compact identities/indexes are held globally."""
from contextlib import ExitStack
from dataclasses import dataclass
import json
from numbers import Integral
from pathlib import Path
import numpy as np
from lightgbm import Sequence
from data_processor.feature_store_contract import FEATURE_COLUMNS
from .artifacts import task_path,write_json,read_json,file_digest,_is_link
from .market_samples import qualify_stock,open_days

DATA_FILES={'features.f32':'<f4','labels.f64':'<f8','dates.i32':'<i4','targets.i32':'<i4','codes.i32':'<i4'}


class DiskFeatureSequence(Sequence):
    def __init__(self,matrix,index,columns,*,batch_size=4096):
        if type(batch_size) is not int or not 1<=batch_size<=4096:raise ValueError('Invalid batch size')
        self.matrix=matrix;self.index=np.asarray(index,dtype=np.int64);self.columns=np.asarray(columns,dtype=np.int64)
        self.batch_size=batch_size
        if (matrix.ndim!=2 or self.index.ndim!=1 or self.columns.ndim!=1 or not len(self.columns)
                or len(set(self.columns))!=len(self.columns) or np.any(self.index<0) or np.any(self.index>=len(matrix))
                or np.any(self.columns<0) or np.any(self.columns>=matrix.shape[1])):raise ValueError('Invalid matrix selection')

    def __len__(self):return len(self.index)

    def __getitem__(self,index):
        scalar=isinstance(index,Integral) and not isinstance(index,(bool,np.bool_))
        if scalar:
            if index<0 or index>=len(self):raise IndexError('Row outside sequence')
            return self.matrix[self.index[index],self.columns].astype(np.float64)
        if isinstance(index,slice):
            start=0 if index.start is None else index.start;stop=len(self) if index.stop is None else index.stop
            if index.step not in (None,1) or start<0 or stop<start or stop-start>self.batch_size:raise ValueError('Unbounded sequence slice')
            selected=self.index[start:min(stop,len(self))]
        elif isinstance(index,(list,np.ndarray)):
            values=np.asarray(index)
            if values.ndim!=1 or values.dtype.kind not in 'iu' or len(values)>self.batch_size:raise ValueError('Unbounded sequence gather')
            if np.any(values<0) or np.any(values>=len(self)):raise IndexError('Row outside sequence')
            selected=self.index[values]
        else:raise TypeError('Sequence index must be integer, slice or list')
        return self.matrix[selected][:,self.columns].astype(np.float64)


@dataclass
class MatrixBundle:
    directory: Path
    rows: int
    feature_names: list
    features: object
    labels: object
    dates: object
    target_dates: object
    codes: object
    splits: dict
    split_metadata: dict
    coverage: dict

    @classmethod
    def open(cls,directory):
        directory=Path(directory);meta=read_json(directory/'matrix.json')
        if not directory.is_dir() or any(_is_link(p) for p in (directory,*directory.parents)):
            raise ValueError('Unsafe matrix directory')
        expected_files={*DATA_FILES,'train.i64','val.i64','test.i64','coverage.json'}
        if (meta.get('schema_version')!=1 or set(meta.get('files',{}))!=expected_files
                or meta['feature_names']!=list(FEATURE_COLUMNS) or type(meta['rows']) is not int or meta['rows']<=0):
            raise ValueError('Invalid matrix schema')
        for name,proof in meta['files'].items():
            if name not in {*DATA_FILES,'train.i64','val.i64','test.i64','coverage.json'}:raise ValueError('Unknown matrix file')
            path=directory/name
            if _is_link(path) or path.stat().st_size!=proof['size'] or file_digest(path)!=proof['sha256']:
                raise ValueError('Matrix integrity mismatch')
        arrays={}
        for name,dtype in DATA_FILES.items():
            shape=(meta['rows'],len(FEATURE_COLUMNS)) if name=='features.f32' else (meta['rows'],)
            if (directory/name).stat().st_size!=np.dtype(dtype).itemsize*int(np.prod(shape)):raise ValueError('Matrix size mismatch')
            arrays[name]=np.memmap(directory/name,dtype=dtype,mode='r',shape=shape)
        splits={}
        for phase in ('train','val','test'):
            count=meta['splits'][phase]['count']
            if type(count) is not int or count<=0 or (directory/(phase+'.i64')).stat().st_size!=count*8:
                raise ValueError('Invalid split index size')
            index=np.memmap(directory/(phase+'.i64'),dtype='<i8',mode='r',shape=(count,))
            if np.any(index<0) or np.any(index>=meta['rows']):raise ValueError('Split index outside data')
            splits[phase]=index
        coverage_path=directory/'coverage.json'
        if coverage_path.stat().st_size>16*1024*1024:raise ValueError('Coverage bound exceeded')
        coverage=read_json(coverage_path,max_bytes=16*1024*1024)
        return cls(directory,meta['rows'],meta['feature_names'],arrays['features.f32'],arrays['labels.f64'],
                   arrays['dates.i32'],arrays['targets.i32'],arrays['codes.i32'],splits,meta['splits'],coverage)


def build_market_matrix(task,stock_iterator,workdir,emit):
    directory=Path(workdir).absolute();task_path(directory.parent,task['id'])
    if directory.exists() or _is_link(directory):raise ValueError('Existing matrix directory')
    directory.mkdir(parents=True,exist_ok=False)
    names=list(FEATURE_COLUMNS);codes=task['binding']['stock_codes'];mapping=task['binding']['stockid2idx']
    if codes!=sorted(set(codes)) or mapping!={code:i for i,code in enumerate(codes)} or len(codes)!=task['binding']['pool_count']:
        raise ValueError('Frozen pool mapping invalid')
    coverage=dict(pool_count=len(codes),pool_identity=task['binding']['pool_identity'],stocks={},daily_eligible_stocks={});rows=0;seen=[]
    with ExitStack() as stack:
        streams={name:stack.enter_context((directory/name).open('xb')) for name in DATA_FILES}
        for code,snapshot,calendar,proof in stock_iterator:
            if len(seen)>=len(codes) or code!=codes[len(seen)]:raise ValueError('Stream changed frozen pool')
            samples,report=qualify_stock(snapshot,calendar,proof,task['request'])
            if not seen:
                coverage['calendar']=calendar
                coverage['daily_eligible_stocks']={str(day):0 for day in open_days(calendar)
                    if task['request']['start_date']<=str(day)<=task['request']['end_date']}
            for day in samples.trade_date:
                key=str(day.date());coverage['daily_eligible_stocks'][key]+=1
            values=samples[names].to_numpy(dtype='<f4')
            if not np.isfinite(values).all():raise ValueError('Non-finite model dtype conversion')
            values.tofile(streams['features.f32'])
            samples.label.to_numpy(dtype='<f8').tofile(streams['labels.f64'])
            for column,path in (('trade_date','dates.i32'),('label_target_date','targets.i32')):
                np.asarray(samples[column],dtype='datetime64[D]').astype('<i4').tofile(streams[path])
            np.full(len(samples),mapping[code],dtype='<i4').tofile(streams['codes.i32'])
            rows+=len(samples);coverage['stocks'][code]=report;seen.append(code)
            if len(seen)%50==0 or len(seen)==len(codes):emit('progress',phase='INPUT',completed=len(seen),total=len(codes))
    if seen!=codes or rows<=0:raise ValueError('Incomplete pool or no eligible samples')
    dates=np.memmap(directory/'dates.i32',dtype='<i4',mode='r',shape=(rows,))
    targets=np.memmap(directory/'targets.i32',dtype='<i4',mode='r',shape=(rows,))
    stockids=np.memmap(directory/'codes.i32',dtype='<i4',mode='r',shape=(rows,))
    unique=np.unique(dates);n=len(unique);first=int(n*.6);second=first+int(n*.2)
    if not 0<first<second<n:raise ValueError('Insufficient whole-date history')
    val_start,test_start=unique[first],unique[second]
    order=np.lexsort((stockids,dates));split_meta={}
    masks=dict(train=(dates<val_start)&(targets<val_start),val=(dates>=val_start)&(dates<test_start)&(targets<test_start),test=dates>=test_start)
    for phase,mask in masks.items():
        index=order[mask[order]]
        if not len(index):raise ValueError('Empty purged split')
        with (directory/(phase+'.i64')).open('xb') as stream:index.astype('<i8').tofile(stream)
        iso=lambda value:str(np.datetime64(int(value),'D'))
        split_meta[phase]=dict(count=len(index),signal_start=iso(dates[index].min()),signal_end=iso(dates[index].max()),label_end=iso(targets[index].max()))
    coverage.update(eligible_rows=rows,purged_rows=rows-sum(v['count'] for v in split_meta.values()))
    write_json(directory/'coverage.json',coverage)
    files=(*DATA_FILES,'train.i64','val.i64','test.i64','coverage.json')
    write_json(directory/'matrix.json',dict(schema_version=1,rows=rows,feature_names=names,splits=split_meta,
        files={name:dict(size=(directory/name).stat().st_size,sha256=file_digest(directory/name)) for name in files}))
    return MatrixBundle.open(directory)
