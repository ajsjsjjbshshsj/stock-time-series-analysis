from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

from preparation_market_support import fixture,build,plan
import pytest


def test_complete_database_and_sources_require_zero_market_requests():
    assert plan(build(*fixture()))==[]


def test_only_one_missing_stock_day_planned_not_all_history():
    pool,window,tables,evidence=fixture()
    tables['stock_daily']=tables['stock_daily'][1:]
    result=plan(build(pool,window,tables,evidence))
    assert [(r['method'],r['start'],r['expected_codes']) for r in result]==[
        ('daily','2026-09-28',['000001.SZ']),('suspend_d','2026-09-28',['000001.SZ'])]


def test_missing_basic_groups_stocks_into_one_date_request():
    pool,window,tables,evidence=fixture()
    tables['stock_daily_basic']=[r for r in tables['stock_daily_basic'] if r['trade_date']!='2026-09-29']
    requests=plan(build(pool,window,tables,evidence))
    assert len(requests)==1
    assert requests[0]['method']=='daily_basic'
    assert requests[0]['expected_codes']==['000001.SZ','600000.SH']
    assert 'ts_code' not in requests[0]['params']


def test_factor_cache_only_requests_missing_date_segment():
    pool,window,tables,evidence=fixture()
    evidence['adj_factor']=evidence['adj_factor'][1:]
    requests=plan(build(pool,window,tables,evidence))
    assert len(requests)==1
    assert requests[0]['method']=='adj_factor'
    assert requests[0]['start']==requests[0]['end']=='2026-09-28'


def test_generic_bj_request_does_not_silently_relax_old_b1_boundary():
    from app.market_data.repair_contract import request
    from app.market_data.repair_acquisition import validate_request
    req=request('daily',['830001.BJ'],'2026-09-28','2026-09-28')
    with pytest.raises(ValueError): validate_request(req)
    validate_request(req,allowed_markets=('SH','SZ','BJ'))


def test_factor_segments_bridge_nontrading_days_but_not_cached_trading_days():
    inventory=dict(window=dict(empty=False),missing_reference=[],missing_daily=[],missing_basic=[],
        missing_suspensions=[],missing_factors=[['000001.SZ','2026-09-25'],['000001.SZ','2026-09-28']],
        attribution=dict(status=[dict(trade_date='2026-09-25'),dict(trade_date='2026-09-28')]))
    requests=plan(inventory)
    assert len(requests)==1
    assert requests[0]['start']=='2026-09-25'
    assert requests[0]['end']=='2026-09-28'

