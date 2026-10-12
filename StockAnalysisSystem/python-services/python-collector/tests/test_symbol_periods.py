"""Independent ticker lifetimes: no replacement-code lookup or price copying."""
import importlib
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))

def module():return importlib.import_module('app.nightly.symbol_periods')

@pytest.mark.parametrize('code,day,expected',[
    ('300114.SZ','2023-01-03',True),('300114.SZ','2025-02-14',True),
    ('300114.SZ','2025-02-17',False),('300114.SZ','2026-09-30',False),
    ('302132.SZ','2023-01-03',False),('302132.SZ','2025-02-14',False),
    ('302132.SZ','2025-02-17',True),('302132.SZ','2026-09-30',True),
])
def test_each_code_is_effective_only_in_its_own_period(code,day,expected):
    assert module().eligible_on(code,day,list_date='2010-08-27') is expected

@pytest.mark.parametrize('day,expected',[
    ('2023-01-03',False),('2026-09-28',False),('2026-09-29',True),('2026-09-30',True),
])
def test_new_stock_starts_at_actual_listing_date(day,expected):
    assert module().eligible_on('301001.SZ',day,list_date='2026-09-29') is expected

def test_listing_date_cannot_be_replaced_by_known_code_start():
    assert not module().eligible_on('302132.SZ','2025-02-17',list_date='2025-02-18')

def test_delisting_day_is_excluded_and_no_pre_2023_rows_are_eligible():
    assert module().eligible_on('600000.SH','2023-01-03',list_date='1999-11-10',delist_date='2023-01-04')
    assert not module().eligible_on('600000.SH','2023-01-04',list_date='1999-11-10',delist_date='2023-01-04')
    assert not module().eligible_on('600000.SH','2022-12-30',list_date='1999-11-10')

@pytest.mark.parametrize('code',['920001.BJ','900001.SH','200001.SZ','000001'])
def test_other_markets_and_bare_codes_are_never_eligible(code):
    assert not module().eligible_on(code,'2026-09-30',list_date='2020-01-01')

def test_period_details_contain_no_partner_code_or_mutable_catalog_reference():
    first=module().period_for('300114.SZ')
    assert first==dict(valid_from=None,valid_until='2025-02-17')
    first['valid_until']=None
    assert module().period_for('300114.SZ')['valid_until']=='2025-02-17'
    assert module().period_for('302132.SZ')==dict(valid_from='2025-02-17',valid_until=None)

@pytest.mark.parametrize('date',['bad','2026-02-30',None])
def test_invalid_dates_fail_instead_of_guessing(date):
    with pytest.raises((ValueError,TypeError)):module().eligible_on('600000.SH',date,list_date='1999-11-10')

def test_new_cohort_applies_code_periods_without_rewriting_old_snapshot():
    from app.nightly.contracts import freeze_market,validate_market
    from nightly_support import reference
    rows=[reference(),reference('300114.SZ',market='创业板',list_date='20100827'),
          reference('302132.SZ',market='创业板',list_date='20100827')]
    old=freeze_market('2023-01-03',rows,'a'*64,'2026-10-11T20:00:00+08:00')
    new=freeze_market('2023-01-03',rows,'a'*64,'2026-10-11T20:00:00+08:00',schema_version=2)
    assert old['codes']==['000001.SZ','300114.SZ','302132.SZ']
    assert new['codes']==['000001.SZ','300114.SZ']
    assert validate_market(old)==old
    assert validate_market(new)==new
    after=freeze_market('2025-02-17',rows,'a'*64,'2026-10-11T20:00:00+08:00',schema_version=2)
    assert after['codes']==['000001.SZ','302132.SZ']

def test_policy_versions_get_distinct_job_identity():
    from app.nightly.contracts import market_identity
    assert market_identity('2023-01-03',requirements_version='market-v2')!=market_identity('2023-01-03')
    with pytest.raises(ValueError):market_identity('2023-01-03',requirements_version='unknown')
