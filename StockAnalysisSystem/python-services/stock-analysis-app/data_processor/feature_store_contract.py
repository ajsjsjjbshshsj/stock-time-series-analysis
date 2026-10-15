"""Pure, versioned storage contract; never imports configuration or opens a DB.

The legacy feature table is deliberately not an accepted destination. Features
remain in their raw scale; training/prediction own the model-specific scaler.
"""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
import re

import numpy as np
import pandas as pd

from data_processor.feature_schema import FEATURE_COL_MAP

STORAGE_CONTRACT = 'traditional_store_v2_62_1'
CANONICAL_TABLE = 'stock_features_v2'
ACTIVE_SLOT = 'traditional_v2'
FEATURE_COLUMNS = tuple(FEATURE_COL_MAP.values())
FEATURE_INPUTS = tuple(FEATURE_COL_MAP)
_CODE = re.compile(r'(?:60|68)[0-9]{4}\.SH|(?:0[0-9]{5}|30[0-9]{4})\.SZ')
_BUILD = re.compile(r'[0-9a-f]{32}')
_ALLOWED_INPUTS = frozenset(FEATURE_INPUTS) | {
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount',
    'pre_close', 'change', 'pct_chg', 'turnover_rate',
    'future_return_1d', 'future_return_5d', 'future_direction_1d',
    'future_direction_5d', 'label',
}


def columns_digest():
    """The order is part of the contract, not merely its set of field names."""
    return hashlib.sha256(json.dumps(FEATURE_COLUMNS, separators=(',', ':')).encode()).hexdigest()


def validate_build_id(build_id):
    if not isinstance(build_id, str) or _BUILD.fullmatch(build_id) is None:
        raise ValueError('Invalid feature build identity')
    return build_id


def build_table_name(build_id):
    return 'stock_features_v2_build_' + validate_build_id(build_id)


def archive_table_name(build_id):
    return 'stock_features_v2_archive_' + validate_build_id(build_id)


def validate_table_name(table):
    if table == CANONICAL_TABLE:
        return table
    for prefix in ('stock_features_v2_build_', 'stock_features_v2_archive_'):
        if isinstance(table, str) and table.startswith(prefix):
            validate_build_id(table[len(prefix):])
            return table
    raise ValueError('Uncontrolled feature table name')


def validate_stock_code(code):
    if not isinstance(code, str) or _CODE.fullmatch(code) is None:
        raise ValueError('Expected canonical SH/SZ A-share code')
    return code


def date_only(value):
    """Reject timezone/time-of-day instead of silently truncating an identity."""
    if isinstance(value, str):
        if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value) is None:
            raise ValueError('Expected ISO date-only identity')
        return date.fromisoformat(value)
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    if value is pd.NaT or value is None or value is pd.NA:
        raise ValueError('Missing date identity')
    if isinstance(value, datetime):
        if value.tzinfo is not None or any((value.hour, value.minute, value.second,
                                           value.microsecond, getattr(value, 'nanosecond', 0))):
            raise ValueError('Expected timezone-free date-only identity')
        return value.date()
    if type(value) is date:
        return value
    raise ValueError('Expected date-only identity')


def _stored_number(value):
    if value is None or value is pd.NA:
        return None
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number, Decimal)):
        raise ValueError('Feature value must be numeric, not a coercible string or boolean')
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Invalid feature numeric value') from None
    if math.isnan(number):
        return None
    if not math.isfinite(number):
        raise ValueError('Non-finite feature value')
    # MySQL numeric storage does not preserve a zero sign; normalize before hashing.
    return 0.0 if number==0.0 else number


def feature_records(frame, build_id, *, start_date=None, cutoff=None):
    """Adapt one whole stock history without dropping warmup/latest rows.

    Raw columns and temporary labels are accepted as inputs but never written.
    Unknown indicators are refused so accidental schema changes cannot silently
    disappear. Bounds belong to the build, not to a hardcoded wall-clock date.
    """
    validate_build_id(build_id)
    if not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any():
        raise ValueError('Expected a frame with unique columns')
    required = {'ts_code', 'trade_date', *FEATURE_INPUTS}
    if not required.issubset(frame.columns) or set(frame.columns) - _ALLOWED_INPUTS:
        raise ValueError('Feature input schema mismatch')
    first = date_only(start_date) if start_date is not None else None
    last = date_only(cutoff) if cutoff is not None else None
    if first is not None and last is not None and first > last:
        raise ValueError('Invalid build date range')
    if frame.empty:
        return []
    codes = [validate_stock_code(c) for c in frame['ts_code']]
    if len(set(codes)) != 1:
        raise ValueError('Feature computation must use one stock history')
    dates = [date_only(d) for d in frame['trade_date']]
    if len(dates) != len(set(dates)):
        raise ValueError('Duplicate feature stock/date key')
    if any((first is not None and d < first) or (last is not None and d > last) for d in dates):
        raise ValueError('Feature dates escape build range')
    # Index positions, not iterrows(), preserve numeric/identity types and never
    # mutate the caller frame or accidentally promote a boolean to a number.
    selected = frame.loc[:, FEATURE_INPUTS]
    values = list(selected.itertuples(index=False, name=None))
    records = []
    for position in sorted(range(len(dates)), key=dates.__getitem__):
        record = {'build_id': build_id, 'ts_code': codes[position], 'trade_date': dates[position]}
        record.update((name, _stored_number(value)) for name, value in zip(FEATURE_COLUMNS, values[position]))
        records.append(record)
    return records
