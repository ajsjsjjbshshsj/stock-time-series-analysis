from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

import pytest
from preparation_market_support import fixture, build


def test_complete_shared_quotes_and_legitimate_null_valuation_are_ready():
    inventory=build(*fixture())
    assert inventory['data_ready'] is True
    assert inventory['stock_count']==2
    assert inventory['issues']['SOURCE_CONFLICT']==0
    assert inventory['missing_daily']==[]
    assert inventory['missing_basic']==[]


def test_full_suspension_explains_missing_quote_without_fake_price():
    pool,window,tables,evidence=fixture()
    tables['stock_daily']=tables['stock_daily'][1:]
    tables['stock_daily_basic']=tables['stock_daily_basic'][1:]
    evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='S',suspend_timing=None)]
    result=build(pool,window,tables,evidence)
    assert result['data_ready'] is True
    assert result['missing_daily']==[]
    assert result['coverage']['FULL_DAY_SUSPENSION']==1


def test_sr_conflict_remains_not_ready_even_when_requests_succeeded():
    pool,window,tables,evidence=fixture()
    tables['stock_daily']=tables['stock_daily'][1:]
    evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type=t,suspend_timing=None) for t in ('S','R')]
    result=build(pool,window,tables,evidence)
    assert result['data_ready'] is False
    assert result['issues']['SOURCE_CONFLICT']==1


def test_listing_boundaries_do_not_shrink_pool():
    pool,window,tables,evidence=fixture()
    tables['stock_basic'][0]['list_date']='2026-09-29'
    tables['stock_daily']=tables['stock_daily'][1:]
    tables['stock_daily_basic']=tables['stock_daily_basic'][1:]
    evidence['adj_factor']=evidence['adj_factor'][1:]
    result=build(pool,window,tables,evidence)
    assert result['stock_count']==2
    assert result['coverage']['PRE_LISTING']==1
    assert result['data_ready'] is True


@pytest.mark.parametrize('value',[None,'nan','0','-1'])
def test_bad_existing_prices_are_issues_not_overwritten(value):
    pool,window,tables,evidence=fixture()
    tables['stock_daily'][0]['close']=value
    result=build(pool,window,tables,evidence)
    assert result['data_ready'] is False
    assert result['issues']['INVALID_EXISTING_DATA']==1
    assert result['missing_daily']==[]


def test_missing_ipo_or_calendar_is_explicit_prerequisite_not_guess():
    pool,window,tables,evidence=fixture()
    tables['stock_basic']=tables['stock_basic'][1:]
    result=build(pool,window,tables,evidence)
    assert result['data_ready'] is False
    assert result['missing_reference']==['000001.SZ']
