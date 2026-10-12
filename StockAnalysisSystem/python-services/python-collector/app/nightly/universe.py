"""Verified current reference partitions, derived into dated A-share cohorts."""
from datetime import datetime
from pathlib import Path
import time
import pandas as pd

from app.market_data.forward_snapshot import read,write,digest
from app.preparation.contracts import TZ,iso,timestamp
from .contracts import FIELDS,market_day,freeze_market,_reference,excluded_legacy_reference
from .acquisition import scoped_path,rows_of,make_pacing,paced_call,lazy_source_client

PARTITIONS=[(exchange,status) for exchange in ('SSE','SZSE') for status in ('L','D','P')]

def _params(exchange,status):return dict(exchange=exchange,list_status=status,fields=','.join(FIELDS))

def _validated(rows,params,acquired):
    if not isinstance(rows,list) or len(rows)>=6000 or params['list_status']=='L' and not rows:
        raise ValueError('REFERENCE_PARTITION_INCOMPLETE')
    for row in rows:
        if row['exchange']!=params['exchange'] or row['list_status']!=params['list_status']:
            raise ValueError('REFERENCE_QUERY_BOUNDARY_INVALID')
        if excluded_legacy_reference(row,acquired) is None:_reference(row,acquired)
        for field in ('list_date','delist_date'):
            value=row[field]
            if value not in (None,'') and (type(value) is not str or len(value)!=8):
                raise ValueError('REFERENCE_NATIVE_DATE_INVALID')
    return rows

def _partition(root,exchange,status):
    path=scoped_path(root,f'{exchange}_{status}.json');raw=path.with_suffix('.parquet');proof=path.with_suffix('.proof.json')
    if read(proof)!=dict(sha256=digest(path),raw_sha256=digest(raw)):raise ValueError('REFERENCE_SHARD_HASH_INVALID')
    body=read(path);params=_params(exchange,status)
    if set(body)!={'params','acquired_at','rows','row_count'} or body['params']!=params or type(body['row_count']) is not int or body['row_count']!=len(body['rows']):
        raise ValueError('REFERENCE_SHARD_BINDING_INVALID')
    acquired=timestamp(body['acquired_at'])
    if acquired.isoformat()!=body['acquired_at']:raise ValueError('REFERENCE_TIMESTAMP_INVALID')
    frame=pd.read_parquet(raw)
    if list(frame.columns)!=list(FIELDS) or frame.columns.duplicated().any() or rows_of(frame)!=body['rows']:
        raise ValueError('REFERENCE_ORIGINAL_DISAGREES')
    _validated(body['rows'],params,acquired)
    return body

def verify_reference(directory):
    root=Path(directory).resolve();manifest=scoped_path(root,'manifest.json');complete=scoped_path(root,'complete.json')
    expected_files={}
    for exchange,status in PARTITIONS:
        for suffix in ('.json','.parquet','.proof.json'):
            name=f'{exchange}_{status}'+suffix;expected_files[name]=digest(scoped_path(root,name))
    binding=dict(schema_version=1,source='tushare.stock_basic',partitions=[_params(e,s) for e,s in PARTITIONS])
    if read(scoped_path(root,'binding.json'))!=binding:raise ValueError('REFERENCE_BINDING_CHANGED')
    expected=dict(schema_version=1,binding_sha256=digest(root/'binding.json'),files=expected_files)
    if read(manifest)!=expected or read(complete)!=dict(manifest_sha256=digest(manifest)):
        raise ValueError('REFERENCE_COMPLETION_CHANGED')
    parts=[_partition(root,e,s) for e,s in PARTITIONS]
    rows=[row for part in parts for row in part['rows']]
    acquired=max(timestamp(part['acquired_at']) for part in parts).isoformat()
    # Validation also catches conflicting identities across separate L/D/P calls.
    freeze_market(timestamp(acquired).date().isoformat(),rows,digest(manifest),acquired)
    exclusions=[item for row in rows if (item:=excluded_legacy_reference(row,timestamp(acquired))) is not None]
    return dict(source_directory=str(root),manifest_sha256=digest(manifest),acquired_at=acquired,rows=rows,excluded_references=exclusions)

class MarketUniverseProvider:
    def __init__(self,client_factory,requests_factory,*,clock=time.time,pinned_reference=None):
        self.client_factory=client_factory;self.requests_factory=requests_factory;self.clock=clock
        self.pinned_reference=pinned_reference;self.on_progress=None

    def acquire_reference(self,directory):
        root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=True)
        complete=scoped_path(root,'complete.json')
        if complete.exists():return verify_reference(root)
        binding=dict(schema_version=1,source='tushare.stock_basic',partitions=[_params(e,s) for e,s in PARTITIONS])
        path=scoped_path(root,'binding.json')
        if path.exists():
            if read(path)!=binding:raise ValueError('REFERENCE_BINDING_CHANGED')
        else:write(path,binding,exclusive=True)
        pacing=None;client=None
        for index,(exchange,status) in enumerate(PARTITIONS):
            if self.on_progress is not None:self.on_progress(index,len(PARTITIONS))
            path=scoped_path(root,f'{exchange}_{status}.json');raw=path.with_suffix('.parquet');proof=path.with_suffix('.proof.json')
            if path.exists() or proof.exists():_partition(root,exchange,status);continue
            if raw.exists():raise ValueError('UNVERIFIED_REFERENCE_RESPONSE_RETAINED')
            if pacing is None:pacing=make_pacing(self.requests_factory,root/'pacing')
            if client is None:client=lazy_source_client(self.client_factory)
            params=_params(exchange,status)
            frame=paced_call(pacing,client.stock_basic,params,self.clock)
            if not isinstance(frame,pd.DataFrame) or frame.columns.duplicated().any() or set(frame.columns)!=set(FIELDS):
                raise ValueError('REFERENCE_RESPONSE_SCHEMA')
            frame=frame[list(FIELDS)].copy();frame.to_parquet(raw,index=False)
            acquired=datetime.fromtimestamp(self.clock(),TZ);rows=rows_of(frame)
            _validated(rows,params,acquired)
            write(path,dict(params=params,acquired_at=acquired.isoformat(),rows=rows,row_count=len(rows)),exclusive=True)
            write(proof,dict(sha256=digest(path),raw_sha256=digest(raw)),exclusive=True)
        manifest=scoped_path(root,'manifest.json')
        files={}
        for exchange,status in PARTITIONS:
            for suffix in ('.json','.parquet','.proof.json'):
                name=f'{exchange}_{status}'+suffix;files[name]=digest(scoped_path(root,name))
        write(manifest,dict(schema_version=1,binding_sha256=digest(root/'binding.json'),files=files),exclusive=True)
        write(complete,dict(manifest_sha256=digest(manifest)),exclusive=True)
        if self.on_progress is not None:self.on_progress(len(PARTITIONS),len(PARTITIONS))
        return verify_reference(root)

    def freeze(self,day,directory):
        if market_day(day)>datetime.fromtimestamp(self.clock(),TZ).date():raise ValueError('MARKET_DAY_IN_FUTURE')
        if self.pinned_reference is not None:
            bundle=verify_reference(self.pinned_reference['source_directory'])
            if bundle!=self.pinned_reference:raise ValueError('PINNED_REFERENCE_CHANGED')
        else:bundle=self.acquire_reference(directory)
        return freeze_market(day,bundle['rows'],bundle['manifest_sha256'],bundle['acquired_at'])
