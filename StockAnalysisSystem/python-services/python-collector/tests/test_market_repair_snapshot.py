"""Catch fabricated suspension, null provenance and source-package tampering."""
import importlib
import pandas as pd
import pytest
from tests.test_market_repair_contract import portable_pool, baseline, inputs
from app.market_data.repair_contract import load_baseline, plan_requests


def module():
    try: return importlib.import_module('app.market_data.repair_snapshot')
    except ModuleNotFoundError: pytest.fail('repair snapshot is not implemented')


def sources(suspensions=None):
    return dict(daily=[],daily_basic=[],adj_factor=[],suspend_d=suspensions or [])


@pytest.mark.parametrize('events,want',[
    ([('S',None)],'FULL_DAY_SUSPENSION'),([('R',None)],'UNRESOLVED_MISSING'),
    ([('S','09:30-10:00')],'UNRESOLVED_MISSING'),([('S',None),('R',None)],'SOURCE_CONFLICT'),
    ([],'UNRESOLVED_MISSING')])
def test_only_unambiguous_full_day_suspension_explains_missing(tmp_path,events,want):
    data=load_baseline(baseline(tmp_path))
    rows=[dict(ts_code='000002.SZ',trade_date='20231009',suspend_type=t,suspend_timing=time) for t,time in events]
    result=module().classify_sources(data,sources(rows))
    status=next(r for r in result['status'] if r['ts_code']=='000002.SZ' and r['trade_date']=='2023-10-09')
    assert status['daily_status']==want
    assert result['report']['model_ready'] is False
    assert not any(r['ts_code']=='000002.SZ' for r in result['daily'])


def test_prelisting_and_all300_identities_preserved(tmp_path):
    result=module().classify_sources(load_baseline(baseline(tmp_path)),sources())
    assert len(result['status'])==900
    assert next(r for r in result['status'] if r['ts_code']=='000300.SZ' and r['trade_date']=='2023-10-09')['daily_status']=='PRE_LISTING'


def test_database_null_not_labeled_as_verified_provider_null(tmp_path):
    result=module().classify_sources(load_baseline(baseline(tmp_path)),sources())
    row=next(r for r in result['status'] if r['ts_code']=='000001.SZ' and r['trade_date']=='2023-10-09')
    assert row['basic_status']=='DATABASE_ROW'
    assert row['pe_status']=='DATABASE_NULL_UNVERIFIED'


def test_new_native_null_and_old_conflict_keep_old_values(tmp_path):
    data=load_baseline(baseline(tmp_path)); src=sources()
    src['daily_basic']=[dict(ts_code='000001.SZ',trade_date='20231009',turnover_rate=1.,pe=10.,pe_ttm=10.,pb=2.,ps=3.,total_mv=100.)]
    result=module().classify_sources(data,src)
    assert result['basic'][0]['pe'] is None
    assert len(result['conflicts'])==1
    src['daily_basic'][0].update(ts_code='000002.SZ',pe=None,pe_ttm=None)
    result=module().classify_sources(data,src)
    row=next(r for r in result['status'] if r['ts_code']=='000002.SZ' and r['trade_date']=='2023-10-09')
    assert row['pe_status']=='PROVIDER_NULL'
    assert result['candidates']['stock_daily_basic'][0]['pe'] is None


def test_new_quotes_outside_listing_calendar_cannot_enter_snapshot(tmp_path):
    data=load_baseline(baseline(tmp_path)); src=sources()
    src['daily']=[dict(ts_code='000300.SZ',trade_date='20231009',open=1.,close=1.,high=1.,low=1.,pre_close=1.,change=0.,pct_chg=0.,vol=1.,amount=1.)]
    with pytest.raises(ValueError): module().classify_sources(data,src)


def test_sealed_package_recomputes_report_and_rejects_tampering(tmp_path):
    from app.market_data.repair_acquisition import collect_requests
    from tests.test_market_repair_acquisition import Clock
    base=baseline(tmp_path); clock=Clock()
    class Empty:
        def __getattr__(self,method):
            def call(**kwargs): return pd.DataFrame(columns=kwargs['fields'].split(','))
            return call
    acq=tmp_path/'acquisition'
    collect_requests(Empty(),plan_requests(base),acq/'initial',tmp_path/'state2.json',.5,clock,clock.sleep)
    collect_requests(Empty(),[],acq/'basic',tmp_path/'state2.json',.5,clock,clock.sleep)
    output=tmp_path/'snapshot'; module().assemble_snapshot(base,acq,output)
    verified=module().verify_snapshot(output)
    assert verified['report']['model_ready'] is False
    assert verified['report']['source_issue_counts']['UNRESOLVED_MISSING']==897
    before={str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    module().assemble_snapshot(base,acq,output)
    assert {str(p.relative_to(output)):p.read_bytes() for p in output.rglob('*') if p.is_file()}==before
    (output/'report.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError): module().verify_snapshot(output)
