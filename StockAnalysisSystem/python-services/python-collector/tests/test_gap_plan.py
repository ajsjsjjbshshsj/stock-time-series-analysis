"""Planning must neither refetch existing rows nor split trading-day runs."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from gap_support import coverage_fixture

CODE = '000001.SZ'
OTHER = '600000.SH'
DAYS = ['2026-09-25', '2026-09-28', '2026-09-29']


def test_complete_snapshot_has_zero_market_requests():
    from app.preparation.gap_plan import plan_daily, plan_basic
    keys = [(CODE, day) for day in DAYS]
    snapshot = coverage_fixture(DAYS, daily_keys=keys, basic_keys=keys)
    assert plan_daily(snapshot) == []
    assert plan_basic(snapshot, []) == []


def test_consecutive_sessions_merge_across_weekend():
    from app.preparation.gap_plan import plan_daily
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER), daily_keys=[(OTHER, day) for day in DAYS])
    requests = plan_daily(snapshot)
    assert len(requests) == 1
    assert requests[0]['mode'] == 'stock_range'
    assert requests[0]['params']['ts_code'] == CODE
    assert requests[0]['params']['start_date'] == '20260925'
    assert requests[0]['params']['end_date'] == '20260929'
    assert 'trade_date' not in requests[0]['params']
    assert requests[0]['expected_keys'] == [[CODE, day] for day in DAYS]
    assert plan_daily(snapshot) == requests


def test_existing_middle_day_splits_range():
    from app.preparation.gap_plan import plan_daily
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER),
        daily_keys=[(CODE, '2026-09-28')]+[(OTHER, day) for day in DAYS])
    assert [(req['start'], req['end']) for req in plan_daily(snapshot)] == [
        ('2026-09-25', '2026-09-25'), ('2026-09-29', '2026-09-29')]


def test_year_and_code_boundaries_split():
    from app.preparation.gap_plan import plan_daily
    days = ['2024-12-31', '2025-01-02', '2025-02-14', '2025-02-17']
    snapshot = coverage_fixture(days, codes=('300114.SZ', '302132.SZ', OTHER),
        daily_keys=[(OTHER, day) for day in days])
    requests = plan_daily(snapshot)
    assert [(r['expected_codes'], r['start'], r['end']) for r in requests] == [
        (['300114.SZ'], '2024-12-31', '2024-12-31'),
        (['300114.SZ'], '2025-01-02', '2025-02-14'),
        (['302132.SZ'], '2025-02-17', '2025-02-17')]


def test_only_basic_missing_does_not_request_daily():
    from app.preparation.gap_plan import plan_daily, plan_basic
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER),
        daily_keys=[(code, day) for code in (CODE, OTHER) for day in DAYS],
        basic_keys=[(OTHER, day) for day in DAYS])
    assert plan_daily(snapshot) == []
    requests = plan_basic(snapshot, [])
    assert len(requests) == 1 and requests[0]['method'] == 'daily_basic'
    assert requests[0]['expected_codes'] == [CODE]


def test_almost_all_missing_is_not_full_date():
    from app.preparation.gap_plan import plan_daily
    snapshot = coverage_fixture(['2026-09-25'], codes=(CODE, OTHER), daily_keys=[(OTHER, '2026-09-25')])
    assert [req['mode'] for req in plan_daily(snapshot)] == ['stock_range']


def test_whole_target_day_missing_allows_bounded_full_date():
    from app.preparation.gap_plan import plan_daily
    requests = plan_daily(coverage_fixture(['2026-09-25'], codes=(CODE, OTHER)))
    assert len(requests) == 1 and requests[0]['mode'] == 'full_date'
    assert requests[0]['params']['trade_date'] == '20260925'
    assert 'ts_code' not in requests[0]['params']


def test_basic_requires_existing_or_actual_new_quotes():
    from app.preparation.gap_plan import plan_basic
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER),
        daily_keys=[(OTHER, day) for day in DAYS], basic_keys=[(OTHER, day) for day in DAYS])
    assert plan_basic(snapshot, []) == []
    requests = plan_basic(snapshot, [(CODE, '2026-09-25')])
    assert requests[0]['expected_keys'] == [[CODE, '2026-09-25']]


def test_basic_followup_does_not_replan_existing_quote_gaps():
    from app.preparation.gap_plan import plan_basic
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER),
        daily_keys=[(CODE, DAYS[0])]+[(OTHER, day) for day in DAYS],
        basic_keys=[(OTHER, day) for day in DAYS])
    requests = plan_basic(snapshot, [(CODE, DAYS[1])], include_existing=False)
    assert [key for request in requests for key in request['expected_keys']] == [[CODE, DAYS[1]]]


def test_mutated_snapshot_and_expanded_request_rejected():
    import pytest
    from app.preparation.gap_plan import plan_daily, make_gap_request
    snapshot = coverage_fixture(DAYS, codes=(CODE, OTHER), daily_keys=[(OTHER, day) for day in DAYS])
    with pytest.raises(ValueError):
        make_gap_request('daily', 'stock_range', [OTHER], [(OTHER, DAYS[0])], snapshot=snapshot)
    snapshot['daily_bits'][CODE] = '7'
    with pytest.raises(ValueError, match='HASH'):
        plan_daily(snapshot)


def test_sparse_followup_does_not_traverse_unrelated_stock_session_matrix():
    from datetime import date,timedelta
    from app.preparation.gap_plan import plan_basic
    from app.preparation.contracts import identity
    days=[(date(2026,9,1)+timedelta(days=i)).isoformat() for i in range(30)]
    snapshot=coverage_fixture(days,codes=tuple(f'{i:06d}.SZ' for i in range(1000)))
    steps=[0]
    class CountedSessions(list):
        def __iter__(self):
            for value in super().__iter__():
                steps[0]+=1
                yield value
    snapshot['sessions']=CountedSessions(snapshot['sessions'])
    snapshot['coverage_sha256']=identity({key:value for key,value in snapshot.items() if key!='coverage_sha256'})
    steps[0]=0
    result=plan_basic(snapshot,[('000001.SZ','2026-09-17')],include_existing=False)
    assert result[0]['expected_keys']==[['000001.SZ','2026-09-17']]
    assert steps[0]<=len(days)*10
