"""Local DB membership snapshots, explicitly distinct from provider schema1."""
import re
from .contracts import META, identity, iso, timestamp, _members
from app.nightly.contracts import is_a_share

INDEX_COUNTS = {'000300.SH': 300, '000852.SH': 1000}


def freeze_local(metadata, members, source_sha256):
    if set(metadata) != set(META): raise ValueError('LOCAL_POOL_METADATA')
    group, code = metadata['group_type'], metadata['group_code']
    for key in ('pool_id', 'group_code', 'display_name'):
        v = metadata[key]
        if type(v) is not str or not v.strip() or len(v) > 128 or any(ord(c) < 32 or c in '/\\' for c in v):
            raise ValueError('LOCAL_POOL_VALUE')
    if group not in ('market', 'industry', 'index') or metadata['pool_id'] != group+':'+code:
        raise ValueError('LOCAL_POOL_GROUP')
    expected = 'local.stock_constituent' if group == 'index' else 'local.stock_basic'
    if metadata['provider'] != expected: raise ValueError('LOCAL_POOL_PROVIDER')
    requested = iso(metadata['requested_as_of'])
    if requested > timestamp(metadata['acquired_at']).date(): raise ValueError('LOCAL_POOL_TIME')
    source_date = metadata['provider_snapshot_date']
    if group == 'index':
        if code not in INDEX_COUNTS or source_date is None or iso(source_date) > requested:
            raise ValueError('LOCAL_INDEX_DATE')
    elif source_date is not None or group == 'market' and code != 'SH_SZ_A':
        raise ValueError('LOCAL_POOL_SOURCE_DATE')
    if type(source_sha256) is not str or re.fullmatch('[0-9a-f]{64}', source_sha256) is None:
        raise ValueError('LOCAL_POOL_SOURCE_HASH')
    normalized = _members(members)
    if len(normalized) != len(members) or any(not is_a_share(r['ts_code']) for r in normalized):
        raise ValueError('LOCAL_POOL_SCOPE')
    if group == 'index' and len(normalized) != INDEX_COUNTS[code]: raise ValueError('LOCAL_INDEX_INCOMPLETE')
    binding = dict(group_type=group, group_code=code, provider=expected,
        source_date=source_date, source_sha256=source_sha256, members=normalized)
    version = 'pool_'+identity(binding)[:32]
    codes = [r['ts_code'] for r in normalized]
    return dict(metadata, schema_version=2, source_sha256=source_sha256, pool_version=version,
        members=normalized, codes=codes, stock_count=len(codes), member_sha256=identity(normalized),
        stockid2idx={c:i for i,c in enumerate(codes)}, survivorship_bias=True)


def validate_local(pool):
    expected = freeze_local({k:pool[k] for k in META}, pool['members'], pool['source_sha256'])
    mapping = pool.get('stockid2idx')
    if (type(pool.get('schema_version')) is not int or type(pool.get('stock_count')) is not int
            or pool.get('survivorship_bias') is not True or type(mapping) is not dict
            or any(type(value) is not int for value in mapping.values()) or pool != expected):
        raise ValueError('LOCAL_POOL_IDENTITY_CHANGED')
    return pool
