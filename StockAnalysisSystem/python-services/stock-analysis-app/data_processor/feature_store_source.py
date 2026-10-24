"""Bounded, portable source proofs for explicit feature builds; no acquisition."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
from itertools import groupby

import numpy as np
import pandas as pd
from sqlalchemy import text

from data_processor.feature_store_contract import date_only, validate_stock_code

SOURCE_COLUMNS = ('ts_code','trade_date','open','high','low','close','vol','amount',
                  'pre_close','change','pct_chg','turnover_rate')
_SOURCE_SELECT = """SELECT d.ts_code,d.trade_date,d.open,d.high,d.low,d.close,d.vol,d.amount,
 d.pre_close,d.`change`,d.pct_chg,b.turnover_rate,
 b.ts_code AS basic_code,m.ts_code AS master_code
 FROM stock_daily d LEFT JOIN stock_daily_basic b
 ON d.ts_code=b.ts_code AND d.trade_date=b.trade_date
 LEFT JOIN stock_basic m ON d.ts_code=m.ts_code"""
SOURCE_SQL = _SOURCE_SELECT + """
 WHERE d.ts_code=:code AND d.trade_date BETWEEN :start AND :cutoff
 ORDER BY d.trade_date"""
SOURCE_RANGE_SQL = _SOURCE_SELECT + """
 WHERE d.ts_code BETWEEN :first_code AND :last_code
 AND d.trade_date BETWEEN :start AND :cutoff
 ORDER BY d.ts_code,d.trade_date"""
SOURCE_STOCK_BATCH_SIZE=64


def json_value(value):
    if isinstance(value, dict):
        return {k:json_value(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        return [json_value(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime,date,np.datetime64)):
        return date_only(value).isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite(): raise ValueError('Non-finite source decimal')
        return format(value,'f')
    if isinstance(value, (float,np.floating)):
        number=float(value)
        if math.isnan(number): return None
        if not math.isfinite(number): raise ValueError('Non-finite source number')
        return number
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, np.bool_): return bool(value)
    return value


def canonical_bytes(value):
    return json.dumps(json_value(value),sort_keys=True,separators=(',',':'),
                      ensure_ascii=False,allow_nan=False).encode('utf-8')


def file_digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def validate_source(frame,start,cutoff):
    start,cutoff=date_only(start),date_only(cutoff)
    if start>cutoff or frame.empty or frame.columns.duplicated().any() or set(frame.columns)!=set(SOURCE_COLUMNS):
        raise ValueError('Invalid feature source schema/range')
    result=frame.loc[:,SOURCE_COLUMNS].copy(deep=True)
    codes=[validate_stock_code(c) for c in result.ts_code]
    if len(set(codes))!=1: raise ValueError('Expected one source stock')
    dates=[date_only(d) for d in result.trade_date]
    if len(set(dates))!=len(dates) or any(not start<=d<=cutoff for d in dates):
        raise ValueError('Source keys escape range or repeat')
    result['trade_date']=dates
    for name in SOURCE_COLUMNS[2:]:
        if any(isinstance(v,(bool,np.bool_)) for v in result[name]):
            raise ValueError('Boolean market data')
        numeric=pd.to_numeric(result[name],errors='raise').astype(float)
        required=name in ('open','high','low','close','vol','amount')
        if (required and not np.isfinite(numeric).all()) or np.isinf(numeric).any():
            raise ValueError('Invalid market numeric value')
        if name in ('open','high','low','close') and (numeric<=0).any():
            raise ValueError('Non-positive market price')
        if name in ('vol','amount','turnover_rate') and (numeric.dropna()<0).any():
            raise ValueError('Negative market volume/valuation')
    prices=result[['open','high','low','close']].astype(float)
    if (prices.high<prices[['open','low','close']].max(axis=1)).any() or (prices.low>prices[['open','high','close']].min(axis=1)).any():
        raise ValueError('Invalid OHLC range')
    return result.sort_values('trade_date').reset_index(drop=True)


def update_source_digest(digest,frame,*,additional_digests=()):
    targets=(digest,*additional_digests)
    for row in frame.loc[:,SOURCE_COLUMNS].itertuples(index=False,name=None):
        payload=canonical_bytes(dict(zip(SOURCE_COLUMNS,row)))+b'\n'
        for target in targets:target.update(payload)


def snapshot_frames(frames,directory,start_date,cutoff):
    """Seal a stream of sorted stock histories without storing the full panel."""
    start_date,cutoff=date_only(start_date),date_only(cutoff)
    if start_date>cutoff: raise ValueError('Invalid source range')
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    digest=hashlib.sha256();shards=[];count=0;previous=''
    for supplied in frames:
        frame=validate_source(supplied,start_date,cutoff)
        code=frame.ts_code.iloc[0]
        if code<=previous: raise ValueError('Source stocks must be unique and sorted')
        previous=code;filename=code+'.parquet';path=directory/filename
        frame.to_parquet(path,index=False)
        stock_hash=hashlib.sha256();update_source_digest(stock_hash,frame,additional_digests=(digest,))
        shards.append(dict(ts_code=code,file=filename,sha256=file_digest(path),
            source_sha256=stock_hash.hexdigest(),row_count=len(frame),
            first_date=str(frame.trade_date.iloc[0]),last_date=str(frame.trade_date.iloc[-1])))
        count+=len(frame)
    if not count: raise ValueError('Source snapshot is empty')
    manifest=dict(schema_version=1,source_start=str(start_date),source_cutoff=str(cutoff),
                  source_sha256=digest.hexdigest(),row_count=count,stock_count=len(shards),shards=shards)
    with (directory/'manifest.json').open('x',encoding='utf-8') as stream:
        json.dump(manifest,stream,ensure_ascii=False,indent=2,allow_nan=False)
    return manifest


def verify_snapshot(manifest,directory):
    directory=Path(directory).resolve()
    saved=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if canonical_bytes(saved)!=canonical_bytes(manifest) or manifest.get('schema_version')!=1:
        raise ValueError('Source manifest identity mismatch')
    digest=hashlib.sha256();count=0;previous=''
    for shard in manifest['shards']:
        code=validate_stock_code(shard['ts_code'])
        path=(directory/shard['file']).resolve()
        if code<=previous or shard['file']!=code+'.parquet' or not path.is_relative_to(directory):
            raise ValueError('Uncontrolled source shard path/order')
        previous=code
        if file_digest(path)!=shard['sha256']: raise ValueError('Source shard hash mismatch')
        frame=validate_source(pd.read_parquet(path),manifest['source_start'],manifest['source_cutoff'])
        local=hashlib.sha256();update_source_digest(local,frame,additional_digests=(digest,))
        if (set(frame.ts_code)!={code} or len(frame)!=shard['row_count']
                or local.hexdigest()!=shard['source_sha256']
                or str(frame.trade_date.iloc[0])!=shard['first_date']
                or str(frame.trade_date.iloc[-1])!=shard['last_date']):
            raise ValueError('Source shard coverage/value mismatch')
        count+=len(frame)
    if count!=manifest['row_count'] or len(manifest['shards'])!=manifest['stock_count'] or digest.hexdigest()!=manifest['source_sha256']:
        raise ValueError('Complete source snapshot mismatch')
    return manifest


def source_frames(connection,start,cutoff):
    """Bounded stock ranges avoid both per-stock queries and a global filesort."""
    with connection.execute(text('SELECT DISTINCT ts_code FROM stock_daily WHERE trade_date BETWEEN :start AND :cutoff ORDER BY ts_code'),
                            dict(start=start,cutoff=cutoff)) as result:
        codes=list(result.scalars())
    for code in codes:validate_stock_code(code)
    statement=text(SOURCE_RANGE_SQL).execution_options(yield_per=1000)
    for offset in range(0,len(codes),SOURCE_STOCK_BATCH_SIZE):
        batch=codes[offset:offset+SOURCE_STOCK_BATCH_SIZE];members=set(batch)
        params=dict(start=start,cutoff=cutoff,first_code=batch[0],last_code=batch[-1])
        with connection.execute(statement,params) as result:
            for code,rows in groupby(result.mappings(),key=lambda row:row['ts_code']):
                if code not in members:raise ValueError('Source stock inventory changed')
                values=[]
                for row in rows:
                    if row['basic_code'] is None or row['master_code'] is None:
                        raise ValueError('Missing market reference or daily-basic counterpart')
                    values.append({name:row[name] for name in SOURCE_COLUMNS})
                yield pd.DataFrame(values,columns=SOURCE_COLUMNS)


def snapshot_source(db,directory,start_date,cutoff):
    start,cutoff=date_only(start_date),date_only(cutoff)
    with db.engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        frames=source_frames(connection,start,cutoff)
        try:
            return snapshot_frames(frames,directory,start,cutoff)
        finally:
            try:frames.close()  # Close an unbuffered result before rollback on errors.
            finally:connection.rollback()
