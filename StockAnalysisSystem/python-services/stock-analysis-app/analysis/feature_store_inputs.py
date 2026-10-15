"""Source-bound research inputs, with labels separate from inference features."""
from copy import deepcopy
from dataclasses import dataclass,field
import hashlib

import numpy as np
import pandas as pd
import pyarrow as pa

from data_processor.feature_store_contract import FEATURE_COLUMNS,STORAGE_CONTRACT,date_only,validate_stock_code
from data_processor.feature_store_source import SOURCE_COLUMNS,canonical_bytes


@dataclass(frozen=True)
class DependencyHistory:
    """Immutable Arrow transport: pandas attrs can copy/compare it cheaply."""
    table: pa.Table=field(compare=False,repr=False)
    sha256: str

    def __deepcopy__(self,memo):
        return self

    def to_frame(self):
        return self.table.to_pandas()


def normalize_prices(frame):
    if not set(SOURCE_COLUMNS).issubset(frame) or frame.columns.duplicated().any():
        raise ValueError('Incomplete feature source prices')
    result=frame.loc[:,SOURCE_COLUMNS].copy()
    result['trade_date']=[date_only(v) for v in result.trade_date]
    result['ts_code']=[validate_stock_code(c) for c in result.ts_code]
    if result.duplicated(['ts_code','trade_date']).any():raise ValueError('Duplicate source keys')
    for c in SOURCE_COLUMNS[2:]:result[c]=pd.to_numeric(result[c],errors='raise').astype(float)
    return result.sort_values(['ts_code','trade_date']).reset_index(drop=True)


def pool_identity(codes):
    codes=sorted(validate_stock_code(c) for c in codes)
    if not codes or len(set(codes))!=len(codes):raise ValueError('Invalid fixed stock mapping')
    return hashlib.sha256(canonical_bytes(codes)).hexdigest()


def calculation_source_digest(prices):
    digest=hashlib.sha256()
    for row in normalize_prices(prices).itertuples(index=False,name=None):
        digest.update(canonical_bytes(dict(zip(SOURCE_COLUMNS,row)))+b'\n')
    return digest.hexdigest()


def build_research_inputs(snapshot,raw_prices,pool_metadata):
    raw=normalize_prices(raw_prices);bound=normalize_prices(snapshot.raw_prices)
    if not raw.equals(bound):raise ValueError('Research prices differ from verified feature source')
    codes=list(pool_metadata.get('stock_codes',[]));identity=pool_metadata.get('pool_identity')
    if not isinstance(identity,str) or not identity or len(codes)!=len(set(codes)) or set(codes)!=set(raw.ts_code):
        raise ValueError('Frozen pool mapping does not match complete source coverage')
    frame=snapshot.frame.copy(deep=True)
    frame['trade_date']=[date_only(v) for v in frame.trade_date]
    if (frame.columns.duplicated().any() or set(frame)!={'ts_code','trade_date',*FEATURE_COLUMNS}
            or frame.duplicated(['ts_code','trade_date']).any()
            or set(zip(frame.ts_code,frame.trade_date))!=set(zip(raw.ts_code,raw.trade_date))):
        raise ValueError('Feature input identity/schema mismatch')
    frame=frame.merge(raw,on=['ts_code','trade_date'],how='inner',validate='one_to_one')
    # Optional sector/name columns belong to a selected pool, never global storage.
    for c in ('industry','name','股票名称'):
        if c in raw_prices:
            extra=raw_prices[['ts_code','trade_date',c]].copy()
            extra['trade_date']=[date_only(v) for v in extra.trade_date]
            frame=frame.merge(extra,on=['ts_code','trade_date'],validate='one_to_one')
    frame['trade_date']=pd.to_datetime(frame.trade_date)
    contract=deepcopy(snapshot.metadata)
    history=normalize_prices(getattr(snapshot,'source_history',snapshot.raw_prices))
    history=history[history.trade_date<=raw.trade_date.max()].copy()
    if set(history.ts_code)!=set(codes):raise ValueError('Missing feature dependency history')
    contract.update(pool_identity=identity,stock_codes=codes,pool_mapping_sha256=pool_identity(codes),
                    feature_history_start=snapshot.metadata['source_start'],
                    source_prefix_start=str(history.trade_date.min()),source_prefix_cutoff=str(history.trade_date.max()),
                    source_prefix_sha256=calculation_source_digest(history))
    frame.attrs['feature_store_contract']=contract
    frame.attrs['feature_store_source_history']=DependencyHistory(
        pa.Table.from_pandas(history,preserve_index=False),contract['source_prefix_sha256'])
    return frame,contract


def training_copy(frame,forward_days=5):
    from analysis.pipeline import compute_labels
    if type(forward_days) is not int or forward_days<1:raise ValueError('Invalid label horizon')
    result=compute_labels(frame,forward_days)
    result['label_target_date']=result.groupby('ts_code').trade_date.shift(-forward_days)
    return result


def inference_on_date(frame,signal_date):
    date=date_only(signal_date)
    result=frame[[date_only(v)==date for v in frame.trade_date]].copy()
    if result.empty or set(result.ts_code)!=set(frame.ts_code) or result.ts_code.duplicated().any():
        raise ValueError('Current signal date has incomplete fixed pool coverage')
    return result


def inference_values(frame,feature_names,signal_date):
    from analysis.pipeline import sanitize_feature_columns
    names=list(feature_names)
    if not names or sanitize_feature_columns(names,frame.columns)!=names:
        raise ValueError('Incomplete inference feature schema')
    latest=inference_on_date(frame,signal_date)
    values=latest[names].apply(pd.to_numeric,errors='raise')
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError('Fixed pool includes immature or invalid features; members cannot be dropped')
    return values


def validate_model_inputs(model_metadata,feature_contract,feature_names,pool_identity,*,raw_prices=None):
    from analysis.pipeline import sanitize_feature_columns
    if not isinstance(model_metadata,dict) or not isinstance(feature_contract,dict) or feature_names is None:
        raise ValueError('Model feature/source contract missing')
    names=list(feature_names)
    if not names or len(set(names))!=len(names) or sanitize_feature_columns(names)!=names:
        raise ValueError('Unsafe model feature order')
    required=('storage_contract','algorithm_version','columns_sha256','code_sha256','pool_identity','pool_mapping_sha256','feature_history_start')
    if (feature_contract.get('storage_contract')!=STORAGE_CONTRACT
            or any(not feature_contract.get(k) or model_metadata.get(k)!=feature_contract[k] for k in required)
            or model_metadata.get('feature_names')!=names or feature_contract.get('pool_identity')!=pool_identity
            or not model_metadata.get('build_id') or not model_metadata.get('source_sha256')):
        raise ValueError('Model feature/source/pool contract mismatch; explicit retraining required')
    if model_metadata['source_sha256']!=feature_contract.get('source_sha256'):
        if raw_prices is None or not all(model_metadata.get(k) for k in ('source_prefix_start','source_prefix_cutoff','source_prefix_sha256')):
            raise ValueError('Model historical source prefix evidence required')
        dependency=raw_prices.attrs.get('feature_store_source_history')
        if dependency is not None and not isinstance(dependency,DependencyHistory):
            raise ValueError('Invalid dependency history transport')
        raw=normalize_prices(dependency.to_frame() if dependency is not None else raw_prices)
        supplied=normalize_prices(raw_prices)
        matching=supplied[['ts_code','trade_date']].merge(raw,on=['ts_code','trade_date'],how='left',validate='one_to_one')
        if not normalize_prices(matching).equals(supplied):
            raise ValueError('Inference source differs from bound dependency history')
        prefix=raw[raw.trade_date.between(date_only(model_metadata['source_prefix_start']),date_only(model_metadata['source_prefix_cutoff']))]
        if prefix.empty or calculation_source_digest(prefix)!=model_metadata['source_prefix_sha256']:
            raise ValueError('Model historical source prefix changed')
    return True


def validate_research_calendar(frame,calendar):
    """Labels may not silently jump missing exchange sessions."""
    from analysis.strategy_dates import validate_session_calendar
    opened=validate_session_calendar(calendar)
    for _,group in frame.groupby('ts_code'):
        dates=sorted({str(date_only(d)) for d in group.trade_date})
        if not dates or dates!=[d for d in opened if dates[0]<=d<=dates[-1]]:
            raise ValueError('Research calendar/session coverage incomplete')
