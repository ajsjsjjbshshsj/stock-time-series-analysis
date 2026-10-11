from copy import deepcopy
import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from nightly_support import reference

def module(): return importlib.import_module('app.nightly.contracts')

def freeze(rows,day='2026-09-28'):
    return module().freeze_market(day,rows,'a'*64,'2026-10-11T20:00:00+08:00')

def test_filters_other_markets_b_shares_and_indices():
    rows=[reference(),reference('600000.SH',exchange='SSE',list_date='19991110'),
        reference('920001.BJ',exchange='BSE',market='北交所'),reference('200001.SZ',curr_type='HKD'),
        reference('900901.SH',exchange='SSE',curr_type='USD'),reference('000001.SH',exchange='SSE'),
        reference('399005.SZ'),reference('510300.SH',exchange='SSE')]
    result=freeze(rows)
    assert result['codes']==['000001.SZ','600000.SH'] and result['stock_count']==2
    assert result['reference_kind']=='current_stock_basic_with_listing_boundaries'
    assert result['members'][0]['list_date']=='1991-04-03'

def test_master_listing_delisting_boundaries_do_not_use_current_status_as_history():
    rows=[reference(),reference('600000.SH',exchange='SSE',list_status='D',delist_date='20260929'),
        reference('600001.SH',exchange='SSE',list_status='D',delist_date='20260928'),
        reference('301001.SZ',market='创业板',list_date='20260929'),reference('000002.SZ',list_status='P')]
    result=freeze(rows)
    assert result['codes']==['000001.SZ','000002.SZ','600000.SH']
    assert result['members'][1]['list_status']=='P'
    assert result['members'][2]['delist_date']=='2026-09-29'

def test_member_order_and_sha_are_stable_when_provider_reorders_rows():
    rows=[reference('600000.SH',exchange='SSE'),reference()]
    assert freeze(rows)['member_sha256']==freeze(list(reversed(rows)))['member_sha256']
    assert freeze(rows)['codes']==['000001.SZ','600000.SH']

@pytest.mark.parametrize('change',[
    dict(ts_code='０００００１.SZ'),dict(symbol='600000'),dict(exchange='SSE'),dict(list_date='bad'),
    dict(list_date='20270101'),dict(list_status='D',delist_date=None),dict(delist_date='19800101'),
    dict(list_status='UNKNOWN'),dict(curr_type=None),dict(name='x'*51)])
def test_invalid_reference_cannot_be_silently_frozen(change):
    with pytest.raises(ValueError): freeze([reference(**change)])

def test_duplicate_reference_even_equal_is_rejected():
    with pytest.raises(ValueError): freeze([reference(),reference()])

@pytest.mark.parametrize('day',['2022-12-31','2026-10-12','20260928'])
def test_cleared_and_future_or_unformatted_days_are_rejected(day):
    with pytest.raises(ValueError): freeze([reference()],day)

def test_empty_target_market_is_not_a_success():
    with pytest.raises(ValueError): freeze([reference('920001.BJ',exchange='BSE',market='北交所')])

def test_snapshot_tamper_is_detected_even_if_codes_still_look_valid():
    value=freeze([reference()]); value['members'][0]['name']='已篡改'
    with pytest.raises(ValueError): module().validate_market(value)

def test_unexpected_fields_and_mismatched_counts_rejected():
    for update in (dict(stock_count=9),dict(stock_count=True),dict(codes=['600000.SH']),dict(extra=True)):
        value=deepcopy(freeze([reference()]));value.update(update)
        with pytest.raises(ValueError): module().validate_market(value)

def test_task_identity_does_not_change_by_random_or_clock_reads():
    value=module().market_identity('2026-09-28')
    assert value==module().market_identity('2026-09-28')
    assert value!=module().market_identity('2026-09-29')
    with pytest.raises(ValueError): module().market_identity('2022-12-31')

@pytest.mark.parametrize('sha,stamp',[('short','2026-10-11T20:00:00+08:00'),('a'*64,'2026-10-11T20:00:00')])
def test_source_proof_identity_and_aware_acquired_time_required(sha,stamp):
    with pytest.raises(ValueError): module().freeze_market('2026-09-28',[reference()],sha,stamp)
