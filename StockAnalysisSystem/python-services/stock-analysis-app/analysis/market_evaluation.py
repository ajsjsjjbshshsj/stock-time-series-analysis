"""Offline frozen-candidate evaluation primitives; no training or acquisition."""
from pathlib import Path,PurePosixPath
import numpy as np
import pandas as pd
from tasks.artifacts import file_digest,read_json,write_json,_is_link
from analysis.market_account import run_market_account,execution_readiness
from analysis.market_diagnostics import score_diagnostics


def check_binding(original,current):
    # Evaluator/eligibility code has an explicit new identity; data, lifecycle,
    # calendar, dependency versions and pool may NOT silently change with it.
    if 'runner_sha256' not in original or 'runner_sha256' not in current:
        raise ValueError('Missing runner identity')
    if {k:v for k,v in original.items() if k!='runner_sha256'}!={k:v for k,v in current.items() if k!='runner_sha256'}:
        raise ValueError('Frozen candidate input binding changed')


def score_stock(model,snapshot,calendar,proof,request,names):
    from tasks.market_samples import qualify_signals
    from data_processor.feature_store_contract import FEATURE_COLUMNS
    if not names or names!=[n for n in FEATURE_COLUMNS if n in names] or model.feature_name()!=names:
        raise ValueError('Frozen model feature identity changed')
    signals,coverage=qualify_signals(snapshot,calendar,proof,request)
    result=signals[['ts_code','trade_date']].copy()
    # Training input was float32 disk storage, read back as float64 by LightGBM.
    # Preserve that quantization instead of silently changing inference inputs.
    values=signals[names].to_numpy(dtype='<f4').astype(np.float64)
    if not np.isfinite(values).all():raise ValueError('Signal model dtype overflow')
    scores=model.predict(values,num_threads=2) if len(values) else np.array([],dtype=float)
    if np.asarray(scores).shape!=(len(values),) or not np.isfinite(scores).all():raise ValueError('Invalid frozen scores')
    result['score']=scores
    return result,coverage


def load_factors(evidence,codes,start,end):
    """Validate each original bundle once, not once for each member stock."""
    groups={}
    for offset in range(0,len(codes),500):
        for ref in evidence.repository.list('adj_factor',codes[offset:offset+500],start,end):
            groups.setdefault(ref['relative_path'],[]).append(ref)
    values={};identities=[]
    for path,refs in sorted(groups.items()):
        body=evidence.store.load(refs,codes=sorted({r['ts_code'] for r in refs}),start=start,end=end)
        identities.extend(dict(id=r['evidence_id'],manifest_sha256=r['manifest_sha256']) for r in refs)
        for row in body['adj_factor']:
            key=(row['ts_code'],row['trade_date']);value=float(row['adj_factor'])
            if key in values and values[key]!=value:raise ValueError('Conflicting execution factors')
            values[key]=value
    frame=pd.DataFrame([dict(ts_code=c,trade_date=d,adj_factor=v) for (c,d),v in sorted(values.items())],columns=['ts_code','trade_date','adj_factor'])
    return frame,identities


def load_limits(store,codes,days):
    """Keep original broad evidence intact; expose only the frozen SH/SZ pool."""
    from data_processor.feature_store_contract import validate_stock_code
    selected={validate_stock_code(c) for c in codes};rows=[]
    for day in days:
        rows.extend(row for row in store.load(day) if row['ts_code'] in selected)
    return pd.DataFrame(rows,columns=['ts_code','trade_date','up_limit','down_limit'])


def build_report(prices,scores,factors,limits,calendar,*,account_sink=None,**kwargs):
    readiness,*_=execution_readiness(prices,factors,limits)
    if readiness['status']!='READY':return readiness
    scenarios={}
    def compact(name,account):
        if account_sink is None:return account
        account_sink(name,account)
        return {k:account[k] for k in ('status','metrics','assumptions')}
    for name,fee,slip in (('gross',0.,0.),('fee3',3.,0.),('fee3_slip10',3.,10.)):
        strategy=run_market_account(prices,scores,factors,limits,calendar,fee_bps=fee,slippage_bps=slip,**kwargs)
        benchmark=run_market_account(prices,scores.assign(score=0.),factors,limits,calendar,
            fee_bps=fee,slippage_bps=slip,**dict(kwargs,top_n=None))
        if strategy['status']!='READY' or benchmark['status']!='READY':
            return dict(status='NOT_READY',missing=strategy.get('missing',[])+benchmark.get('missing',[]))
        scenarios[name]=dict(strategy=compact(name+'/strategy',strategy),benchmark=compact(name+'/benchmark',benchmark))
    return dict(status='READY',scenarios=scenarios,published=False,
        warning='Retrospective research on a frozen pool; not live fills or an untouched test set')


def seal_evaluation(directory,report):
    directory=Path(directory).absolute()
    if any(_is_link(p) for p in (directory,*directory.parents)):raise ValueError('Unsafe evaluation directory')
    if (directory/'manifest.json').exists() or (directory/'complete.json').exists():raise FileExistsError('Existing evaluation seal')
    write_json(directory/'report.json',report)
    files={}
    for path in sorted(directory.rglob('*')):
        if _is_link(path):raise ValueError('Unsafe evaluation file')
        if path.is_file():files[path.relative_to(directory).as_posix()]=file_digest(path)
    write_json(directory/'manifest.json',dict(schema_version=1,files=files))
    write_json(directory/'complete.json',dict(manifest_sha256=file_digest(directory/'manifest.json')))


def verify_evaluation(directory):
    directory=Path(directory).absolute()
    if any(_is_link(p) for p in (directory,*directory.parents)):raise ValueError('Unsafe evaluation directory')
    manifest=read_json(directory/'manifest.json',max_bytes=8*1024*1024)
    if read_json(directory/'complete.json')!=dict(manifest_sha256=file_digest(directory/'manifest.json')):
        raise ValueError('Evaluation seal changed')
    if set(manifest)!={'schema_version','files'} or manifest['schema_version']!=1 or not isinstance(manifest['files'],dict):
        raise ValueError('Invalid evaluation manifest')
    actual=set()
    for path in directory.rglob('*'):
        if _is_link(path):raise ValueError('Unsafe evaluation file')
        if path.is_file() and path.name not in ('manifest.json','complete.json'):actual.add(path.relative_to(directory).as_posix())
    if actual!=set(manifest['files']):raise ValueError('Evaluation file inventory changed')
    for name,sha in manifest['files'].items():
        relative=PurePosixPath(name)
        if not name or '\\' in name or ':' in name or relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Unsafe evaluation reference')
        path=directory/name
        if path.resolve().parent!=directory.resolve() and not path.resolve().is_relative_to(directory.resolve()):raise ValueError('Evaluation escape')
        if file_digest(path)!=sha:raise ValueError('Evaluation file changed')
    report=read_json(directory/'report.json',max_bytes=128*1024*1024)
    if report.get('status') not in ('READY','NOT_READY') or report.get('published',False) is not False:
        raise ValueError('Invalid evaluation status')
    if report['status']=='NOT_READY' and ('metrics' in report or 'scenarios' in report):raise ValueError('Unready evaluation contains performance')
    return report
