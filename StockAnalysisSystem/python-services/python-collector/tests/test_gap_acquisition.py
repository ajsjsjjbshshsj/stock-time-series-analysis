"""Original-query provenance, scope validation and bounded provider failures."""
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd
import pytest
from gap_support import coverage_fixture
from test_nightly_acquisition import quote, Pacing
from app.preparation.gap_plan import plan_daily, plan_basic
from app.market_data.repair_contract import DAILY, BASIC

DAY = '2026-09-28'
NOW = 1791770400


def inputs(method='daily', days=(DAY,)):
    daily = [('600000.SH', day) for day in days]
    if method == 'daily_basic':
        daily += [('000001.SZ', day) for day in days]
    snapshot = coverage_fixture(list(days), codes=('000001.SZ', '600000.SH'), daily_keys=daily,
        basic_keys=[('600000.SH', day) for day in days])
    return snapshot, (plan_daily(snapshot) if method == 'daily' else plan_basic(snapshot, []))


def acquirer(root, rows, calls, method='daily'):
    from app.preparation.gap_acquisition import GapAcquirer
    class Client:
        def daily(self, **params):
            calls.append(params)
            return pd.DataFrame(rows, columns=DAILY)
        def daily_basic(self, **params):
            calls.append(params)
            return pd.DataFrame(rows, columns=BASIC)
    return GapAcquirer(lambda: Client(), lambda directory: Pacing(),
        source_store=SimpleNamespace(root=root), clock=lambda: NOW)


def test_stock_range_params_and_response_scope(tmp_path):
    snapshot, requests = inputs()
    calls = []
    result = acquirer(tmp_path/'store', [quote()], calls).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert result['complete'] and result['api_requests'] == 1
    assert calls == [dict(ts_code='000001.SZ', start_date='20260928', end_date='20260928', fields=','.join(DAILY))]
    assert result['shards'][0]['rows'] == [quote()]


def test_completed_shard_reuses_without_sdk(tmp_path):
    from app.preparation.gap_acquisition import GapAcquirer
    snapshot, requests = inputs()
    acquirer(tmp_path/'store', [quote()], []).collect(requests, tmp_path/'raw', snapshot=snapshot)
    def forbidden(*args):
        raise AssertionError('SDK_ON_COMPLETED_SOURCE')
    result = GapAcquirer(forbidden, forbidden, source_store=SimpleNamespace(root=tmp_path/'store'),
        clock=lambda: NOW).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert result['api_requests'] == 0 and result['cache_hits'] == 1


def test_empty_range_is_unresolved_not_fabricated(tmp_path):
    snapshot, requests = inputs()
    result = acquirer(tmp_path/'store', [], []).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert result['complete'] and result['empty_requests'] == 1
    assert result['shards'][0]['rows'] == []
    assert result['shards'][0]['request_complete'] is True
    assert result['shards'][0]['data_ready'] is False


def test_raw_tamper_and_wrong_code_rejected(tmp_path):
    from app.preparation.gap_acquisition import verify_gap_shard
    snapshot, requests = inputs()
    with pytest.raises(ValueError):
        acquirer(tmp_path/'store', [quote('000002.SZ')], []).collect(requests, tmp_path/'wrong', snapshot=snapshot)
    acquirer(tmp_path/'store', [quote()], []).collect(requests, tmp_path/'raw', snapshot=snapshot)
    raw = next((tmp_path/'raw'/'shards').glob('*.parquet'))
    raw.write_bytes(b'changed')
    with pytest.raises(ValueError, match='HASH'):
        verify_gap_shard(tmp_path/'raw', requests[0], snapshot=snapshot)


def test_null_basic_values_preserved(tmp_path):
    snapshot, requests = inputs('daily_basic')
    row = dict(ts_code='000001.SZ', trade_date='20260928', turnover_rate=1., pe=None,
        pe_ttm=None, pb=1., ps=1., total_mv=1000.)
    result = acquirer(tmp_path/'store', [row], [], 'daily_basic').collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert result['shards'][0]['rows'] == [row]


def test_full_date_retains_non_a_raw_but_only_returns_target_candidates(tmp_path):
    snapshot = coverage_fixture([DAY])
    requests = plan_daily(snapshot)
    rows = [quote(), quote('430047.BJ')]
    result = acquirer(tmp_path/'store', rows, []).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert result['shards'][0]['rows'] == [quote()]
    assert result['shards'][0]['exclusions'][0]['ts_code'] == '430047.BJ'
    raw = next((tmp_path/'raw'/'shards').glob('*.parquet'))
    assert list(pd.read_parquet(raw)['ts_code']) == ['000001.SZ', '430047.BJ']


def test_partial_cache_only_satisfies_proven_keys(tmp_path):
    from app.preparation.gap_acquisition import lookup_cached_gap
    snapshot, requests = inputs()
    store = SimpleNamespace(root=tmp_path/'store')
    acquirer(store.root, [quote()], []).collect(requests, tmp_path/'first', snapshot=snapshot)
    wider, wider_requests = inputs(days=('2026-09-28', '2026-09-29'))
    assert lookup_cached_gap(wider_requests[0], snapshot=wider, source_store=store) is None


def test_verified_parent_cache_keeps_original_query(tmp_path):
    from app.preparation.gap_acquisition import GapAcquirer
    wide, queries = inputs(days=('2026-09-28', '2026-09-29'))
    rows = [quote(), dict(quote(), trade_date='20260929')]
    store = SimpleNamespace(root=tmp_path/'store')
    acquirer(store.root, rows, []).collect(queries, tmp_path/'parent', snapshot=wide)
    narrow, requests = inputs()
    def forbidden(*args):
        raise AssertionError('SDK_ON_PROVEN_CACHE')
    result = GapAcquirer(forbidden, forbidden, source_store=store, clock=lambda: NOW).collect(
        requests, tmp_path/'child', snapshot=narrow)
    assert result['api_requests'] == 0 and result['cache_hits'] == 1
    shard = result['shards'][0]
    assert shard['rows'] == [quote()]
    assert shard['parent']['request']['params']['end_date'] == '20260929'
    assert shard['parent']['raw_sha256']


def test_mutated_parent_cache_not_promoted(tmp_path):
    from app.preparation.gap_acquisition import lookup_cached_gap
    snapshot, requests = inputs()
    store = SimpleNamespace(root=tmp_path/'store')
    acquirer(store.root, [quote()], []).collect(requests, tmp_path/'parent', snapshot=snapshot)
    next((tmp_path/'parent'/'shards').glob('*.parquet')).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='HASH'):
        lookup_cached_gap(requests[0], snapshot=snapshot, source_store=store)


@pytest.mark.parametrize('rows', [
    [quote()]*6000, [quote(), quote()], [dict(quote(), trade_date='20260927')],
    [dict(quote(), close=float('inf'))], [quote('302132.SZ')],
])
def test_truncated_duplicate_calendar_nonfinite_or_relabelled_rows_rejected(tmp_path, rows):
    snapshot, requests = inputs()
    with pytest.raises(ValueError):
        acquirer(tmp_path/'store', rows, []).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert list((tmp_path/'raw'/'shards').glob('*.parquet'))
    assert not (tmp_path/'raw'/'complete.json').exists()


@pytest.mark.parametrize('kind, expected_attempts', [('PERMISSION', 1), ('NETWORK', 3)])
def test_permission_and_http502_use_bounded_shared_retry(tmp_path, kind, expected_attempts):
    from app.preparation.gap_acquisition import GapAcquirer
    from app.market_data.forward_snapshot import Requests
    from app.market_data.checked_tushare_api import TushareRequestError
    from app.preparation.service import PreparationFailure
    snapshot, requests = inputs()
    calls = []
    ticks = [NOW]
    def sleep(seconds):
        ticks[0] += seconds
    class Client:
        def daily(self, **params):
            calls.append(params)
            raise TushareRequestError(kind, 502 if kind == 'NETWORK' else None)
    def pacing(directory):
        directory.mkdir(parents=True, exist_ok=True)
        return Requests(directory, .5, lambda: ticks[0], sleep)
    with pytest.raises(PreparationFailure) as error:
        GapAcquirer(lambda: Client(), pacing, source_store=SimpleNamespace(root=tmp_path/'store'),
            clock=lambda: ticks[0]).collect(requests, tmp_path/'raw', snapshot=snapshot)
    assert len(calls) == expected_attempts
    assert 'token' not in str(error.value).lower()
