"""Sealed native day responses using the existing shared request limiter."""
from datetime import datetime
from pathlib import Path
import re,time
import pandas as pd

from app.market_data.forward_snapshot import read,write,digest
from app.market_data.repair_contract import DAILY,BASIC,FACTOR,SUSPEND,request as repair_request
from app.market_data.repair_acquisition import validate_rows
from app.preparation.contracts import TZ,identity,iso,timestamp
from app.preparation.service import PreparationFailure
from app.preparation.acquisition import LazyClient
from app.market_data.checked_tushare_api import TushareRequestError,KINDS
from .contracts import market_day,is_a_share,validate_market

FIELDS={'daily':DAILY,'daily_basic':BASIC,'adj_factor':FACTOR,'suspend_d':SUSPEND}
CAPACITY={'daily':6000,'daily_basic':6000,'adj_factor':None,'suspend_d':5000}

def scoped_path(root,relative):
    root=Path(root).resolve();path=(root/relative).resolve()
    if not path.is_relative_to(root):raise ValueError('SOURCE_PATH_OUTSIDE_ROOT')
    return path

def rows_of(frame):return frame.astype(object).where(frame.notna(),None).to_dict('records')

def _raise_failure(metadata):
    raise PreparationFailure(metadata[0],retry_not_before=metadata[1],http_status=metadata[2]) from None

def make_pacing(factory,directory):
    failure=None
    try:return factory(directory)
    except PreparationFailure as error:failure=(error.kind,error.retry_not_before,error.http_status)
    if failure is not None:_raise_failure(failure)

def lazy_source_client(factory):
    def construct():
        failure=None
        try:return factory()
        except PreparationFailure as error:failure=(error.kind,error.http_status)
        kind=failure[0] if failure[0] in KINDS else 'PROVIDER'
        raise TushareRequestError(kind,failure[1]) from None
    # The shared request lock and quota check run before credential/client setup.
    return LazyClient(construct)

def paced_call(pacing,method,params,clock):
    failure=None
    try:return pacing.call(method,params)
    except PreparationFailure as error:failure=(error.kind,error.retry_not_before,error.http_status)
    except RuntimeError:
        state=getattr(pacing,'state',{})
        shared=getattr(pacing,'shared',None)
        if shared is not None and Path(shared).with_suffix('.lock').exists() and (state.get('retry_not_before') or 0)<=clock():
            failure=('REQUEST_STATE_BUSY',clock()+60,None)
        else:failure=(state.get('failure','PROVIDER'),state.get('retry_not_before'),state.get('http_status'))
    if failure is not None:_raise_failure(failure)

def native_request(method,day,query_codes=None):
    target=market_day(day)
    if method not in FIELDS:raise ValueError('NATIVE_METHOD_INVALID')
    if query_codes is not None:
        if (not isinstance(query_codes,list) or not query_codes or query_codes!=sorted(set(query_codes))
                or any(not is_a_share(code) for code in query_codes) or len(query_codes)>500
                or method in ('daily_basic','adj_factor') and len(query_codes)!=1):
            raise ValueError('NATIVE_QUERY_SCOPE_INVALID')
    fields=FIELDS[method];params=dict(trade_date=target.strftime('%Y%m%d'),fields=','.join(fields))
    if query_codes is not None:params['ts_code']=','.join(query_codes)
    body=dict(method=method,day=day,params=params,query_codes=query_codes,fields=fields,capacity=CAPACITY[method])
    return dict(body,id=identity(body))

def validate_native_day(method,day,rows,query_codes=None):
    r=native_request(method,day,query_codes)
    if not isinstance(rows,list):raise ValueError('NATIVE_ROWS_INVALID')
    if not rows and query_codes is None and method in ('daily','daily_basic','adj_factor'):
        raise ValueError('EMPTY_FULL_MARKET_RESPONSE')
    for row in rows:
        if not isinstance(row,dict) or set(row)!=set(r['fields']) or type(row['ts_code']) is not str or re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',row['ts_code']) is None:
            raise ValueError('NATIVE_ROW_IDENTITY_INVALID')
        if row['trade_date']!=r['params']['trade_date'] or query_codes is not None and row['ts_code'] not in query_codes:
            raise ValueError('NATIVE_ROW_OUTSIDE_REQUEST')
    codes=sorted({row['ts_code'] for row in rows}) or query_codes or ['000001.SZ']
    validation=dict(repair_request(method,codes,day,day),capacity=r['capacity'])
    validate_rows(rows,validation)
    return rows

def verified_shard(directory,r):
    root=Path(directory).resolve();path=scoped_path(root,f"shards/{r['id']}.json")
    raw=path.with_suffix('.parquet');proof=path.with_suffix('.proof.json')
    if read(proof)!=dict(sha256=digest(path),raw_sha256=digest(raw)):raise ValueError('NATIVE_SHARD_HASH_INVALID')
    body=read(path)
    if set(body)!={'request','acquired_at','row_count','rows'} or body['request']!=r or type(body['row_count']) is not int or body['row_count']!=len(body['rows']):
        raise ValueError('NATIVE_SHARD_BINDING_INVALID')
    acquired=timestamp(body['acquired_at'])
    if acquired.isoformat()!=body['acquired_at'] or acquired.date()<iso(r['day']):raise ValueError('NATIVE_SOURCE_TIME_INVALID')
    frame=pd.read_parquet(raw)
    if frame.columns.duplicated().any() or list(frame.columns)!=r['fields'] or rows_of(frame)!=body['rows']:
        raise ValueError('NATIVE_RAW_DISAGREES_WITH_PROOF')
    validate_native_day(r['method'],r['day'],body['rows'],r['query_codes'])
    return dict(body,method=r['method'],params=r['params'],raw_sha256=digest(raw),manifest_sha256=digest(path),
        relative_path=str(path.relative_to(root)).replace('\\','/'),source_directory=str(root))

class NativeDayAcquirer:
    def __init__(self,client_factory,requests_factory,clock=time.time):
        self.client_factory=client_factory;self.requests_factory=requests_factory;self.clock=clock
        self.on_progress=None

    def collect(self,day,methods,directory,query_codes=None):
        market_day(day)
        if not isinstance(methods,(list,tuple)) or len(set(methods))!=len(methods):raise ValueError('NATIVE_METHOD_PLAN_INVALID')
        if not methods:return dict(complete=True,shards=[])
        requests=[native_request(method,day,query_codes) for method in methods]
        root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=True)
        shards_dir=scoped_path(root,'shards');shards_dir.mkdir(exist_ok=True)
        binding=dict(schema_version=1,requests=requests)
        bind_path=scoped_path(root,'binding.json')
        if bind_path.exists():
            if read(bind_path)!=binding:raise ValueError('NATIVE_PLAN_CHANGED')
        else:write(bind_path,binding,exclusive=True)
        complete=scoped_path(root,'complete.json');manifest=scoped_path(root,'manifest.json')
        if complete.exists():
            expected=dict(schema_version=1,binding_sha256=digest(bind_path),files={p.name:digest(p) for p in sorted(shards_dir.iterdir()) if p.is_file()})
            if read(manifest)!=expected or read(complete)!=dict(manifest_sha256=digest(manifest)):raise ValueError('NATIVE_COMPLETION_CHANGED')
        shards=[];pacing=None;client=None
        for index,r in enumerate(requests):
            if self.on_progress is not None:self.on_progress(index,len(requests))
            path=scoped_path(root,f"shards/{r['id']}.json");raw=path.with_suffix('.parquet');proof=path.with_suffix('.proof.json')
            if path.exists() or proof.exists():
                shards.append(verified_shard(root,r));continue
            if complete.exists() or raw.exists():raise ValueError('UNVERIFIED_SOURCE_RESPONSE_RETAINED')
            if pacing is None:pacing=make_pacing(self.requests_factory,root/'pacing')
            if client is None:client=lazy_source_client(self.client_factory)
            frame=paced_call(pacing,getattr(client,r['method']),r['params'],self.clock)
            if not isinstance(frame,pd.DataFrame) or frame.columns.duplicated().any() or set(frame.columns)!=set(r['fields']):raise ValueError('NATIVE_RESPONSE_SCHEMA')
            frame=frame[r['fields']].copy();frame.to_parquet(raw,index=False)
            rows=rows_of(frame);validate_native_day(r['method'],day,rows,query_codes)
            body=dict(request=r,acquired_at=datetime.fromtimestamp(self.clock(),TZ).isoformat(),row_count=len(rows),rows=rows)
            write(path,body,exclusive=True);write(proof,dict(sha256=digest(path),raw_sha256=digest(raw)),exclusive=True)
            shards.append(verified_shard(root,r))
        if not complete.exists():
            write(manifest,dict(schema_version=1,binding_sha256=digest(bind_path),files={p.name:digest(p) for p in sorted(shards_dir.iterdir()) if p.is_file()}),exclusive=True)
            write(complete,dict(manifest_sha256=digest(manifest)),exclusive=True)
        if self.on_progress is not None:self.on_progress(len(requests),len(requests))
        return dict(complete=True,shards=shards)

def day_exclusions(snapshot,shards,*,reference_directory=None):
    """Audit verified inactive factors or explicitly expired code periods.

    Some supplier responses repeat pre-effective prices under a current code.
    Exclude that code's invalid period only, never relabel those prices. Other
    out-of-roster daily/basic rows remain errors, including ordinary delistings.
    """
    validate_market(snapshot);day=snapshot['trade_date'];target=set(snapshot['codes'])
    known=None;excluded=[]
    for shard in shards:
        body=verified_shard(shard['source_directory'],shard['request'])
        if body!=shard or body['request']['day']!=day:raise ValueError('NATIVE_REGISTRY_BINDING_CHANGED')
        outsiders=sorted({row['ts_code'] for row in body['rows'] if is_a_share(row['ts_code']) and row['ts_code'] not in target})
        if not outsiders:continue
        if snapshot['schema_version']!=2 or reference_directory is None:
            raise PreparationFailure('SOURCE_CONFLICT')
        if known is None:
            from .universe import verify_reference
            from .contracts import _reference,excluded_legacy_reference
            from .symbol_periods import reference_rows
            bundle=verify_reference(reference_directory)
            if bundle['manifest_sha256']!=snapshot['source_manifest_sha256']:raise ValueError('REFERENCE_SOURCE_IDENTITY_INVALID')
            acquired=timestamp(bundle['acquired_at'])
            known={row['ts_code']:_reference(row,acquired) for row in reference_rows(bundle['rows'])
                   if excluded_legacy_reference(row,acquired) is None}
        from .symbol_periods import eligible_on,period_for
        for code in outsiders:
            row=known.get(code)
            if row is None or eligible_on(code,day,list_date=row['list_date'],delist_date=row['delist_date']):
                raise PreparationFailure('SOURCE_CONFLICT')
            period=period_for(code)
            outside_explicit_period=(period['valid_from'] is not None and day<period['valid_from']
                or period['valid_until'] is not None and day>=period['valid_until'])
            if body['method']!='adj_factor' and not outside_explicit_period:raise PreparationFailure('SOURCE_CONFLICT')
            excluded.append(dict(method=body['method'],ts_code=code,reason='KNOWN_OUTSIDE_SYMBOL_PERIOD',raw_sha256=body['raw_sha256']))
    return excluded


def register_day_evidence(store,snapshot,shards,*,owner_guard=None,reference_directory=None,exclusion_sink=None):
    validate_market(snapshot);day=snapshot['trade_date'];target=set(snapshot['codes']);refs=[]
    exclusions=day_exclusions(snapshot,shards,reference_directory=reference_directory)
    if exclusion_sink is not None:exclusion_sink(exclusions)
    for shard in shards:
        body=verified_shard(shard['source_directory'],shard['request'])
        if body!=shard or body['request']['day']!=day:raise ValueError('NATIVE_REGISTRY_BINDING_CHANGED')
        codes=sorted(target & set(body['request']['query_codes'])) if body['request']['query_codes'] is not None else snapshot['codes']
        if not codes:raise ValueError('SOURCE_QUERY_OUTSIDE_UNIVERSE')
        rows=[row for row in body['rows'] if row['ts_code'] in target]
        refs.extend(store.register_verified(dict(kind=body['method'],provider='tushare.'+body['method'],
            start_date=day,end_date=day,expected_codes=codes,rows=rows),owner_guard=owner_guard))
    return refs

def supplemental_requests(inventory,max_requests=100):
    if type(max_requests) is not int or not 0<=max_requests<=100:raise ValueError('SOURCE_BUDGET_INVALID')
    requests=[];daily={}
    for code,day in inventory.get('missing_daily',[]):daily.setdefault(day,set()).add(code)
    for day in sorted(daily):
        codes=sorted(daily[day])
        for offset in range(0,len(codes),500):requests.append(dict(method='daily',day=day,query_codes=codes[offset:offset+500]))
    for code,day in sorted({tuple(key) for key in inventory.get('missing_basic',[])}):
        requests.append(dict(method='daily_basic',day=day,query_codes=[code]))
    return dict(requests=requests[:max_requests],exhausted=len(requests)>max_requests,unplanned_requests=max(0,len(requests)-max_requests))
