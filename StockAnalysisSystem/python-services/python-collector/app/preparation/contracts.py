"""Configuration-free frozen pool identities and strict public inputs."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import re

TZ = timezone(timedelta(hours=8))
CODE = re.compile(r'\d{6}\.(SH|SZ|BJ)')
META = ('pool_id', 'display_name', 'group_type', 'group_code', 'provider',
        'requested_as_of', 'provider_snapshot_date', 'acquired_at')


def identity(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def iso(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('ISO_DATE_REQUIRED')
    return date.fromisoformat(value)


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('AWARE_TIMESTAMP_REQUIRED')
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('AWARE_TIMESTAMP_REQUIRED')
    return stamp.astimezone(TZ)


def _metadata(metadata):
    if set(metadata) != set(META):
        raise ValueError('POOL_METADATA_FIELDS')
    for key in ('pool_id', 'display_name', 'group_code'):
        value = metadata[key]
        if (not isinstance(value, str) or not value.strip() or len(value) > 128
                or any(ord(c) < 32 for c in value) or '/' in value or '\\' in value):
            raise ValueError('POOL_METADATA_VALUE')
    group = metadata['group_type']
    provider = {'index': 'tushare.index_weight', 'industry': 'akshare.eastmoney'}.get(group)
    if provider is None or metadata['provider'] != provider:
        raise ValueError('POOL_SOURCE_MISMATCH')
    if metadata['pool_id'] != f"{group}:{metadata['group_code']}":
        raise ValueError('POOL_ID_MISMATCH')
    requested = iso(metadata['requested_as_of'])
    acquired = timestamp(metadata['acquired_at'])
    if requested > acquired.date():
        raise ValueError('POOL_REQUEST_IN_FUTURE')
    source_date = metadata['provider_snapshot_date']
    if group == 'industry' and source_date is not None:
        raise ValueError('INDUSTRY_HAS_NO_HISTORICAL_SNAPSHOT_DATE')
    if group == 'index' and (source_date is None or iso(source_date) > requested):
        raise ValueError('INDEX_SOURCE_DATE_INVALID')


def _members(members):
    if not isinstance(members, list) or not members:
        raise ValueError('POOL_MEMBERS_REQUIRED')
    unique = {}
    for item in members:
        if not isinstance(item, dict) or not {'ts_code'} <= set(item) <= {'ts_code', 'weight'}:
            raise ValueError('POOL_MEMBER_FIELDS')
        code, weight = item['ts_code'], item.get('weight')
        if type(code) is not str or CODE.fullmatch(code) is None:
            raise ValueError('POOL_MEMBER_CODE')
        if weight is not None and (type(weight) not in (float, int) or not math.isfinite(weight) or weight < 0):
            raise ValueError('POOL_MEMBER_WEIGHT')
        row = dict(ts_code=code, weight=weight)
        if code in unique and unique[code] != row:
            raise ValueError('CONFLICTING_POOL_MEMBER')
        unique[code] = row
    return [unique[c] for c in sorted(unique)]


def freeze_pool(metadata: dict, members: list[dict]) -> dict:
    _metadata(metadata)
    members = _members(members)
    binding = {k: metadata[k] for k in ('group_type', 'group_code', 'provider', 'provider_snapshot_date')}
    version = 'pool_' + identity(dict(binding, members=members))[:32]
    codes = [r['ts_code'] for r in members]
    return dict(metadata, schema_version=1, pool_version=version, members=members,
                member_sha256=identity(members), stock_count=len(codes), codes=codes,
                stockid2idx={code: i for i, code in enumerate(codes)}, survivorship_bias=True)


def validate_pool(pool: dict) -> dict:
    if not isinstance(pool, dict):
        raise ValueError('POOL_REQUIRED')
    if type(pool.get('schema_version')) is int and pool['schema_version'] == 2:
        from .local_pools import validate_local
        return validate_local(pool)
    expected = freeze_pool({k: pool[k] for k in META}, pool['members'])
    if 'legacy_source_manifest_sha256' in pool:
        source_sha = pool['legacy_source_manifest_sha256']
        manifest_sha = pool.get('legacy_manifest_sha256')
        if (pool['group_type'] != 'index' or pool['group_code'] != '000300.SH'
                or pool['stock_count'] != 300 or not isinstance(source_sha, str)
                or re.fullmatch('[0-9a-f]{64}', source_sha) is None
                or not isinstance(manifest_sha, str) or re.fullmatch('[0-9a-f]{64}', manifest_sha) is None):
            raise ValueError('LEGACY_POOL_BINDING')
        raw_members = [dict(index_code='000300.SH', con_code=r['ts_code'],
                            trade_date=pool['provider_snapshot_date'].replace('-', ''), weight=r['weight'])
                       for r in expected['members']]
        expected['pool_version'] = 'csi300_' + identity(dict(index_code='000300.SH',
            provider_snapshot_date=pool['provider_snapshot_date'], members=raw_members,
            source_sha256=source_sha))[:16]
        expected.update(legacy_source_manifest_sha256=source_sha, legacy_manifest_sha256=manifest_sha)
    if expected != pool:
        raise ValueError('POOL_IDENTITY_OR_MAPPING_MISMATCH')
    return pool
