"""Verified qualified-stock inputs and task-local copies of complete disk matrices."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import shutil
import numpy as np
import pandas as pd
from data_processor.feature_store_contract import FEATURE_COLUMNS
from .contracts import digest
from .reuse import ContentStore,read_parquet,safe_path


@dataclass
class PreparedSamples:
    samples: object
    report: dict


def feature_fingerprint(frame):
    if list(frame.columns)!=['ts_code','trade_date',*FEATURE_COLUMNS] or frame.duplicated(['ts_code','trade_date']).any():
        raise ValueError('Invalid reusable matrix feature keys')
    numeric=frame[list(FEATURE_COLUMNS)].copy()
    for name in FEATURE_COLUMNS:
        if numeric[name].dtype.kind not in 'fiu':
            if numeric[name].isna().all():numeric[name]=np.nan
            else:raise ValueError('Invalid feature dtype')
    keys=list(zip(frame.ts_code,pd.to_datetime(frame.trade_date).dt.strftime('%Y-%m-%d')))
    values=numeric.to_numpy(dtype='<f8',copy=True)
    if np.isinf(values).any():raise ValueError('Infinite feature input')
    values[np.isnan(values)]=np.nan;values[values==0.]=0.
    sha=hashlib.sha256();sha.update(digest(keys).encode());sha.update(values.tobytes())
    return sha.hexdigest()


def _algorithm():
    from importlib.metadata import version
    from config.settings import TECHNICAL_INDICATORS
    root=Path(__file__).resolve().parents[1]
    names=('tasks/matrix_cache.py','tasks/market_samples.py','tasks/disk_matrix.py','tasks/market_lifecycle.py',
        'analysis/feature_store_inputs.py','data_processor/feature_engineer.py','data_processor/feature_store_contract.py','data_processor/feature_schema.py')
    return digest(dict(files={name:(root/name).read_text(encoding='utf-8') for name in names},parameters=TECHNICAL_INDICATORS,
        dependencies={name:version(name) for name in ('pandas','numpy','TA-Lib')}))


def _stock_input(task,code,snapshot,calendar,proof,algorithm):
    from workflows.source import history_fingerprint
    prefix=history_fingerprint(getattr(snapshot,'source_history',snapshot.raw_prices),task['request']['end_date'])
    key=dict(kind='qualified_stock_v1',algorithm=algorithm,request=task['request'],code=code,
        policy={k:task['binding'].get(k) for k in ('training_dependencies','runner_sha256','storage_contract','algorithm_version','code_sha256','columns_sha256','source_start','source_cutoff')},
        feature_sha256=feature_fingerprint(snapshot.frame),history_sha256=prefix,calendar_sha256=digest(calendar),proof_sha256=digest(proof))
    return key,prefix


def _samples(task,snapshot,entry):
    folder,metadata=entry
    names=['ts_code','trade_date','label_target_date','label',*FEATURE_COLUMNS]
    frame=read_parquet(folder/'samples.parquet',max_rows=len(snapshot.frame),max_columns=len(names))
    if list(frame.columns)!=names:raise ValueError('Reusable sample columns changed')
    for name in ('trade_date','label_target_date'):frame[name]=pd.to_datetime(frame[name])
    values=frame[['label',*FEATURE_COLUMNS]].to_numpy(dtype=float)
    if (frame.duplicated(['ts_code','trade_date']).any() or not np.isfinite(values).all()
            or len(frame)!=metadata['coverage']['eligible_rows']
            or set(frame.ts_code)-set(snapshot.frame.ts_code)
            or not frame.trade_date.between(pd.Timestamp(task['request']['start_date']),pd.Timestamp(task['request']['end_date'])).all()
            or (frame.label_target_date<frame.trade_date).any()):raise ValueError('Reusable qualified samples changed')
    return PreparedSamples(frame,metadata['coverage'])


def build_reusable_matrix(task,stock_iterator,workdir,emit,store,*,prefixes,activity=None):
    from .disk_matrix import build_market_matrix,MatrixBundle,DATA_FILES
    from .market_samples import qualify_stock
    algorithm=_algorithm();identity=dict(kind='market_matrix_v1',algorithm=algorithm,request=task['request'],binding_sha256=digest(task['binding']))
    cached=store.get(identity);stock_store=ContentStore(store.root/'stocks',max_files=2,max_file_bytes=64*1024**2)
    codes=task['binding']['stock_codes'];observed=[];inputs={};hits=0
    def prepared():
        nonlocal hits
        for code,snapshot,calendar,proof in stock_iterator:
            if activity is not None:activity('CACHE_READ',stock=code,cache_hits=hits)
            if len(observed)>=len(codes) or codes[len(observed)]!=code:raise ValueError('Reusable matrix pool changed')
            observed.append(code);key,prefix=_stock_input(task,code,snapshot,calendar,proof,algorithm);prefixes[code]=prefix;inputs[code]=digest(key)
            if cached is not None:
                if cached[1]['stock_inputs'].get(code)!=inputs[code]:raise ValueError('Current matrix input changed despite bound identity')
                hits+=1
                continue
            entry=stock_store.get(key)
            if entry is None:
                samples,report=qualify_stock(snapshot,calendar,proof,task['request'])
                def build(folder):
                    clean=samples.copy();clean.attrs={};clean.to_parquet(folder/'samples.parquet',index=False)
                    return dict(coverage=report)
                entry=stock_store.publish(key,build)
            else:hits+=1
            yield code,_samples(task,snapshot,entry),calendar,proof
        if observed!=codes:raise ValueError('Incomplete reusable matrix source')
    stream=prepared();directory=safe_path(workdir)
    if cached is not None:
        # Consume every current source/features/history check; a stored build id
        # alone cannot vouch for mutable SQL rows. No qualification or rewrite.
        for _ in stream:pass
        if inputs!=cached[1]['stock_inputs']:raise ValueError('Matrix member coverage changed')
        if activity is not None:activity('CACHE_READ',cache_hits=hits,force=True)
        directory.mkdir(parents=True,exist_ok=False)
        for name in ('matrix.json','coverage.json',*DATA_FILES,'train.i64','val.i64','test.i64'):
            shutil.copyfile(safe_path(cached[0]/name),safe_path(directory/name))
        bundle=MatrixBundle.open(directory);bundle.reused=True;bundle.reused_stocks=len(codes)
        emit('progress',phase='INPUT',completed=len(codes),total=len(codes))
        return bundle
    if activity is not None:activity('MATRIX_WRITE',force=True)
    bundle=build_market_matrix(task,stream,directory,emit)
    def publish(folder):
        for name in ('matrix.json','coverage.json',*DATA_FILES,'train.i64','val.i64','test.i64'):
            shutil.copyfile(safe_path(directory/name),safe_path(folder/name))
        return dict(stock_inputs=inputs)
    store.publish(identity,publish)
    bundle.reused=False;bundle.reused_stocks=hits
    return bundle
