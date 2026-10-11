"""Fixed identities must survive retries, changing dates and source ordering."""
import importlib
from copy import deepcopy

import pytest


def api():
    try:
        return importlib.import_module('app.preparation.contracts')
    except ModuleNotFoundError:
        pytest.fail('fixed pool contracts not implemented')


def metadata(**changes):
    result = dict(pool_id='industry:银行', display_name='银行', group_type='industry',
                  group_code='银行', provider='akshare.eastmoney',
                  requested_as_of='2026-10-11', provider_snapshot_date=None,
                  acquired_at='2026-10-11T17:00:00+08:00')
    return dict(result, **changes)


@pytest.mark.parametrize('size', [2, 300, 1000])
def test_arbitrary_size_and_sorted_mapping(size):
    members = [{'ts_code': f'{i:06d}.SZ'} for i in range(size, 0, -1)]
    pool = api().freeze_pool(metadata(), members)
    assert pool['stock_count'] == size
    assert pool['codes'][0] == '000001.SZ'
    assert pool['stockid2idx']['000001.SZ'] == 0
    assert pool['stockid2idx'][f'{size:06d}.SZ'] == size - 1
    assert pool['provider_snapshot_date'] is None
    assert api().validate_pool(pool) == pool


def test_retry_timestamp_and_source_order_do_not_create_new_version():
    members = [{'ts_code': '600000.SH'}, {'ts_code': '000001.SZ'}]
    first = api().freeze_pool(metadata(), members)
    retry = api().freeze_pool(metadata(acquired_at='2026-10-12T17:00:00+08:00',
                                      requested_as_of='2026-10-12'), members[::-1])
    assert retry['pool_version'] == first['pool_version']
    changed = api().freeze_pool(metadata(), [{'ts_code': '600001.SH'}])
    assert changed['pool_version'] != first['pool_version']


@pytest.mark.parametrize('members', [[], [{'ts_code': 'SECRET'}],
    [{'ts_code': '000001.SZ', 'weight': 1}, {'ts_code': '000001.SZ', 'weight': 2}],
    [{'ts_code': '000001.SZ', 'weight': float('nan')}],
    [{'ts_code': '000001.SZ', 'weight': True}]])
def test_invalid_or_conflicting_members_rejected(members):
    with pytest.raises(ValueError):
        api().freeze_pool(metadata(), members)


def test_identical_duplicates_are_canonicalized_not_double_counted():
    pool = api().freeze_pool(metadata(), [{'ts_code': '000001.SZ'}] * 2)
    assert pool['stock_count'] == 1


@pytest.mark.parametrize('change', [dict(group_type='all'), dict(requested_as_of='20261011'),
    dict(acquired_at='2026-10-11T17:00:00'), dict(provider_snapshot_date='2026-10-10'),
    dict(pool_id='../escape'), dict(provider='tushare.index_weight')])
def test_metadata_cannot_lie_about_source_or_time(change):
    with pytest.raises(ValueError):
        api().freeze_pool(metadata(**change), [{'ts_code': '000001.SZ'}])


@pytest.mark.parametrize('field,value', [('stock_count', 999), ('member_sha256', '0'*64),
    ('pool_version', 'pool_'+'0'*32), ('stockid2idx', {'000001.SZ': 5})])
def test_tampered_pool_rejected(field, value):
    pool = deepcopy(api().freeze_pool(metadata(), [{'ts_code': '000001.SZ'}]))
    pool[field] = value
    with pytest.raises(ValueError):
        api().validate_pool(pool)


def test_legacy_import_keeps_frozen_version_and_mapping():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'stock-analysis-app/models/universes/csi300_fixed/csi300_5739ddc829ad3650'
    if not root.is_dir():
        pytest.skip('local frozen evidence absent; no live acquisition')
    try:
        sources = importlib.import_module('app.preparation.pool_sources')
    except ModuleNotFoundError:
        pytest.fail('pool source adapter not implemented')
    pool = sources.import_legacy_pool(root)
    assert pool['pool_version'] == 'csi300_5739ddc829ad3650'
    assert pool['stock_count'] == 300
    assert pool['stockid2idx']['000001.SZ'] == 0
    assert pool['legacy_manifest_sha256'] == '9518e41d70de390fec845908000a67b78289e46d90065b340b1500e0cb7c0cf8'

