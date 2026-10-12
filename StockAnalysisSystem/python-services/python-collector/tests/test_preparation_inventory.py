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

def intraday_fixture(*,quote=True,timing='11:13-11:23'):
    from app.market_data.repair_contract import DAILY
    pool,window,tables,evidence=fixture()
    original=tables['stock_daily'].pop(0)
    if quote:
        evidence['daily']=[{k:original[k] if k in ('ts_code','trade_date') else float(original[k]) for k in DAILY}]
    evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='S',suspend_timing=timing),
        dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='R',suspend_timing=None)]
    return pool,window,tables,evidence

def test_precise_intraday_stop_and_resume_with_verified_quote_is_not_a_conflict():
    result=build(*intraday_fixture())
    assert result['data_ready'] and result['issues']['SOURCE_CONFLICT']==0
    assert result['attribution']['candidates']['stock_daily'][0]['ts_code']=='000001.SZ'

def test_intraday_stop_without_quote_does_not_invent_full_day_suspension():
    result=build(*intraday_fixture(quote=False))
    assert not result['data_ready'] and result['issues']['SOURCE_CONFLICT']==1
    assert result['attribution']['candidates']['stock_daily']==[]

@pytest.mark.parametrize('timing',[None,'','unknown','25:00-25:10','11:23-11:13','09:30-15:00','08:00-08:30'])
def test_unknown_or_full_session_sr_events_remain_conflicting_even_with_quote(timing):
    result=build(*intraday_fixture(timing=timing))
    assert not result['data_ready'] and result['issues']['SOURCE_CONFLICT']==1
    assert result['attribution']['candidates']['stock_daily']==[]

def test_frozen_repair_default_policy_keeps_original_conservative_replay():
    from app.market_data.repair_snapshot import classify_sources
    pool,window,tables,evidence=intraday_fixture()
    data=dict(pool=pool,tables=tables,calendar=evidence['calendar'])
    sources={method:evidence[method] for method in ('daily','daily_basic','adj_factor','suspend_d')}
    assert classify_sources(data,sources)['report']['source_issue_counts']['SOURCE_CONFLICT']==1
    assert classify_sources(data,sources,allow_verified_intraday=True)['report']['source_issue_counts']['SOURCE_CONFLICT']==0

@pytest.mark.parametrize('intervals',[
    ['09:30-11:30','13:00-15:00'],
    ['09:30-10:30','10:30-11:30','13:00-14:00','14:00-15:00'],
    ['09:30-10:45','10:30-13:45','13:30-15:00'],
    ['09:30-13:30','13:00-15:00'],
])
def test_combined_stops_covering_both_sessions_are_not_intraday_exception(intervals):
    pool,window,tables,evidence=intraday_fixture()
    evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='S',suspend_timing=value) for value in intervals]
    evidence['suspend_d'].append(dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='R',suspend_timing=None))
    result=build(pool,window,tables,evidence)
    assert not result['data_ready'] and result['issues']['SOURCE_CONFLICT']==1
    assert result['attribution']['candidates']['stock_daily']==[]

def test_multiple_intraday_stops_with_real_trading_gap_remain_eligible():
    pool,window,tables,evidence=intraday_fixture()
    evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='S',suspend_timing=value)
        for value in ('09:30-11:20','13:00-15:00')]
    evidence['suspend_d'].append(dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type='R',suspend_timing=None))
    assert build(pool,window,tables,evidence)['data_ready'] is True
