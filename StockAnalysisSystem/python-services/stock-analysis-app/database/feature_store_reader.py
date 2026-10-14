"""Read-only verified traditional features. Never repair or fall back on read."""
from dataclasses import dataclass
import hashlib
import json

import pandas as pd
from sqlalchemy import text,inspect
from sqlalchemy import types

from data_processor.feature_store_contract import (ACTIVE_SLOT,CANONICAL_TABLE,
    STORAGE_CONTRACT,FEATURE_COLUMNS,columns_digest,date_only,validate_build_id,
    validate_table_name,validate_stock_code)
from data_processor.feature_store_source import (SOURCE_COLUMNS,SOURCE_SQL,
    validate_source,update_source_digest)


class FeatureStoreUnavailable(ValueError):
    """An explicit build/publication is required; no silent legacy replacement."""


@dataclass(frozen=True)
class FeatureSnapshot:
    frame: pd.DataFrame
    metadata: dict
    raw_prices: pd.DataFrame


def metadata_row(connection,build_id=None):
    condition='build_id=:identity' if build_id else 'active_slot=:identity'
    row=connection.execute(text('SELECT * FROM stock_feature_build WHERE '+condition),
                           dict(identity=build_id or ACTIVE_SLOT)).mappings().one_or_none()
    if row is None: raise FeatureStoreUnavailable('No verified feature build')
    meta=dict(row)
    validate_build_id(meta['build_id'])
    manifest=meta['source_manifest']
    meta['source_manifest']=json.loads(manifest) if isinstance(manifest,str) else manifest
    return meta


def validate_metadata(meta):
    from data_processor.feature_store_builder import algorithm_identity
    version,code,_=algorithm_identity()
    manifest=meta['source_manifest']
    if (meta['storage_contract']!=STORAGE_CONTRACT or meta['columns_sha256']!=columns_digest()
            or meta['algorithm_version']!=version or meta['code_sha256']!=code
            or manifest.get('schema_version')!=1
            or manifest['source_sha256']!=meta['source_sha256']
            or manifest['source_start']!=str(meta['source_start'])
            or manifest['source_cutoff']!=str(meta['source_cutoff'])
            or manifest['row_count']!=meta['row_count'] or manifest['stock_count']!=meta['stock_count']):
        raise FeatureStoreUnavailable('Feature storage/algorithm/source contract mismatch')


def validate_table(connection,table,build_id):
    validate_table_name(table); validate_build_id(build_id)
    schema=inspect(connection)
    columns={c['name']:c for c in schema.get_columns(table)}
    names={'build_id','ts_code','trade_date',*FEATURE_COLUMNS}
    if (set(columns)!=names or schema.get_pk_constraint(table)['constrained_columns']!=['ts_code','trade_date']
            or any(not isinstance(columns[c]['type'],types.DOUBLE) or not columns[c]['nullable'] for c in FEATURE_COLUMNS)
            or not isinstance(columns['build_id']['type'],types.CHAR) or columns['build_id']['type'].length!=32
            or not isinstance(columns['ts_code']['type'],types.VARCHAR) or columns['ts_code']['type'].length!=10
            or not isinstance(columns['trade_date']['type'],types.DATE)
            or any(columns[c]['nullable'] for c in ('build_id','ts_code','trade_date'))):
        raise FeatureStoreUnavailable('Feature table schema mismatch')
    identities=connection.execute(text(f'SELECT DISTINCT build_id FROM `{table}`')).scalars().all()
    if identities!=[build_id]:raise FeatureStoreUnavailable('Feature table publication identity mismatch')


def stock_source(connection,code,meta):
    rows=connection.execute(text(SOURCE_SQL),dict(code=code,start=meta['source_start'],cutoff=meta['source_cutoff'])).mappings().all()
    if not rows or any(r['basic_code'] is None or r['master_code'] is None for r in rows):
        raise FeatureStoreUnavailable('Feature source/reference coverage missing')
    frame=validate_source(pd.DataFrame([{c:r[c] for c in SOURCE_COLUMNS} for r in rows]),meta['source_start'],meta['source_cutoff'])
    evidence=next((s for s in meta['source_manifest']['shards'] if s['ts_code']==code),None)
    digest=hashlib.sha256();update_source_digest(digest,frame)
    if evidence is None or len(frame)!=evidence['row_count'] or digest.hexdigest()!=evidence['source_sha256']:
        raise FeatureStoreUnavailable('Raw source changed since feature build')
    return frame,evidence


def load_feature_snapshot(session,stock_codes=None,start_date=None,end_date=None,expected_keys=None):
    """Caller owns one bounded read transaction; labels never enter this reader."""
    try:
        connection=session.connection()
        meta=metadata_row(connection)
        if meta['state']!='READY':raise FeatureStoreUnavailable('Feature build is not READY')
        validate_metadata(meta);validate_table(connection,CANONICAL_TABLE,meta['build_id'])
        start=date_only(start_date or meta['source_start']);end=date_only(end_date or meta['source_cutoff'])
        if start>end or start<meta['source_start'] or end>meta['source_cutoff']:
            raise FeatureStoreUnavailable('Requested range outside verified feature source')
        codes=([s['ts_code'] for s in meta['source_manifest']['shards']] if stock_codes is None else list(stock_codes))
        if not codes or len(set(codes))!=len(codes):raise FeatureStoreUnavailable('Invalid requested feature pool')
        codes=sorted(validate_stock_code(c) for c in codes)
        features=[];prices=[];evidence={};all_keys=set()
        fields=','.join('`'+c+'`' for c in ('ts_code','trade_date',*FEATURE_COLUMNS))
        for code in codes:
            raw,proof=stock_source(connection,code,meta)
            raw=raw[raw.trade_date.between(start,end)].copy()
            rows=connection.execute(text(f'SELECT {fields} FROM `{CANONICAL_TABLE}` WHERE build_id=:id AND ts_code=:code AND trade_date BETWEEN :start AND :end ORDER BY trade_date'),dict(id=meta['build_id'],code=code,start=start,end=end)).mappings().all()
            frame=pd.DataFrame(rows,columns=('ts_code','trade_date',*FEATURE_COLUMNS))
            keys=set(zip(raw.ts_code,raw.trade_date))
            if keys!=set(zip(frame.ts_code,frame.trade_date)):
                raise FeatureStoreUnavailable('Requested feature key coverage missing')
            all_keys.update(keys)
            for c in SOURCE_COLUMNS[2:]:raw[c]=pd.to_numeric(raw[c]).astype(float)
            features.append(frame);prices.append(raw);evidence[code]=proof
        if expected_keys is not None and {(validate_stock_code(c),date_only(d)) for c,d in expected_keys}!=all_keys:
            raise FeatureStoreUnavailable('Caller source keys differ from verified scope')
        # A metadata/table switch at any point is fail-closed, never legacy data.
        after=metadata_row(connection)
        if after['build_id']!=meta['build_id']:raise FeatureStoreUnavailable('Feature publication changed during read')
        validate_table(connection,CANONICAL_TABLE,meta['build_id'])
        metadata={k:meta[k] for k in ('build_id','storage_contract','algorithm_version','columns_sha256','code_sha256','source_sha256')}
        metadata.update(source_start=str(meta['source_start']),source_cutoff=str(meta['source_cutoff']),
                        feature_names=list(FEATURE_COLUMNS),source_evidence=evidence)
        return FeatureSnapshot(pd.concat(features,ignore_index=True),metadata,pd.concat(prices,ignore_index=True))
    except FeatureStoreUnavailable:raise
    except Exception:
        raise FeatureStoreUnavailable('Feature store unavailable; explicit operator verification required') from None
