import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pandas as pd
import pytest
from nightly_support import reference
from nightly_store_support import database,store,submit,claim,NOW,OWNER
from app.nightly.contracts import freeze_market
from app.market_data.repair_contract import DAILY,BASIC,FACTOR,SUSPEND

class Pacing:
    def call(self,method,params):return method(**params)

def module():return importlib.import_module('app.nightly.acquisition')

def quote(code='000001.SZ'):
    return dict(ts_code=code,trade_date='20260928',open=10.0,high=11.0,low=9.0,close=10.5,
        pre_close=10.0,change=.5,pct_chg=5.0,vol=100.0,amount=1000.0)

def factor_client(rows,calls):
    class Client:
        def adj_factor(self,**params):
            calls.append(params)
            assert params==dict(trade_date='20260928',fields=','.join(FACTOR))
            return pd.DataFrame(rows,columns=FACTOR)
    return Client()

@pytest.mark.parametrize('outside,known,allowed',[
    ('600000.SH',True,True),('600001.SH',False,False),('000002.SZ',True,False),
])
def test_only_proven_out_of_period_factor_rows_can_be_excluded(tmp_path,outside,known,allowed):
    from app.nightly.universe import MarketUniverseProvider
    from app.preparation.service import PreparationFailure
    master=[reference(),reference('600000.SH',exchange='SSE',list_status='D',delist_date='20200101')]
    if known and outside=='000002.SZ':master.append(reference('000002.SZ'))
    class Client:
        def stock_basic(self,**params):
            from app.nightly.contracts import FIELDS
            return pd.DataFrame([r for r in master if r['exchange']==params['exchange'] and r['list_status']==params['list_status']],columns=FIELDS)
    # Every L partition must be nonempty, independent of retired references.
    master.append(reference('600002.SH',exchange='SSE'))
    provider=MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=lambda:1791709200,schema_version=2)
    bundle=provider.acquire_reference(tmp_path/'reference')
    snapshot=freeze_market('2026-09-28',[reference()]+[reference('600002.SH',exchange='SSE')],'a'*64,'2026-10-11T20:00:00+08:00',schema_version=2)
    snapshot['source_manifest_sha256']=bundle['manifest_sha256']
    acquired=module().NativeDayAcquirer(lambda:factor_client([dict(ts_code='000001.SZ',trade_date='20260928',adj_factor=1.0),dict(ts_code=outside,trade_date='20260928',adj_factor=2.0)],[]),lambda directory:Pacing(),clock=lambda:1790596800).collect('2026-09-28',['adj_factor'],tmp_path/'raw')
    if allowed:
        exclusions=module().day_exclusions(snapshot,acquired['shards'],reference_directory=tmp_path/'reference')
        assert exclusions==[dict(method='adj_factor',ts_code=outside,reason='KNOWN_OUTSIDE_SYMBOL_PERIOD',raw_sha256=acquired['shards'][0]['raw_sha256'])]
        assert len(acquired['shards'][0]['rows'])==2
    else:
        with pytest.raises(PreparationFailure,match='SOURCE_CONFLICT'):
            module().day_exclusions(snapshot,acquired['shards'],reference_directory=tmp_path/'reference')

def test_bulk_factor_one_day_request_and_completed_proof_recovers_without_sdk(tmp_path):
    calls=[];rows=[dict(ts_code='000001.SZ',trade_date='20260928',adj_factor=1.5)]
    acq=module().NativeDayAcquirer(lambda:factor_client(rows,calls),lambda directory:Pacing(),clock=lambda:1790596800)
    result=acq.collect('2026-09-28',['adj_factor'],tmp_path)
    assert result['complete'] and result['shards'][0]['rows']==rows
    assert len(calls)==1 and 'ts_code' not in calls[0]
    def forbidden():raise AssertionError('SDK_ON_COMPLETE_SOURCE')
    recovered=module().NativeDayAcquirer(forbidden,lambda directory:Pacing(),clock=lambda:1790596801).collect('2026-09-28',['adj_factor'],tmp_path)
    assert recovered['shards'][0]['raw_sha256']==result['shards'][0]['raw_sha256']

def test_empty_method_plan_does_not_create_client_or_requests(tmp_path):
    def forbidden(*args):raise AssertionError('UNNEEDED_SOURCE')
    assert module().NativeDayAcquirer(forbidden,forbidden).collect('2026-09-28',[],tmp_path)==dict(complete=True,shards=[])

@pytest.mark.parametrize('method,code,day,allowed',[
    ('daily','302132.SZ','2023-01-03',True),
    ('daily_basic','302132.SZ','2023-01-03',True),
    ('daily','300114.SZ','2025-02-17',True),
    ('daily','600000.SH','2023-01-03',False),
    ('daily_basic','600001.SH','2023-01-03',False),
])
def test_explicit_code_period_excludes_supplier_relabelled_history_only(tmp_path,method,code,day,allowed):
    from app.nightly.universe import MarketUniverseProvider
    from app.nightly.contracts import FIELDS
    from app.preparation.service import PreparationFailure
    master=[reference(),reference('600002.SH',exchange='SSE'),
            reference('600000.SH',exchange='SSE',list_status='D',delist_date='20200101'),
            reference('302132.SZ',market='创业板',list_date='20100827')]
    row=quote(code) if method=='daily' else dict(ts_code=code,trade_date='',turnover_rate=1.,pe=1.,pe_ttm=1.,pb=1.,ps=1.,total_mv=1000.)
    row['trade_date']=day.replace('-','')
    class Client:
        def stock_basic(self,**params):
            return pd.DataFrame([r for r in master if r['exchange']==params['exchange'] and r['list_status']==params['list_status']],columns=FIELDS)
        def daily(self,**params):return pd.DataFrame([row],columns=DAILY)
        def daily_basic(self,**params):return pd.DataFrame([row],columns=BASIC)
    provider=MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=lambda:1791709200,schema_version=2)
    snapshot=provider.freeze(day,tmp_path/'reference')
    source=module().NativeDayAcquirer(lambda:Client(),lambda directory:Pacing(),clock=lambda:1791709200).collect(day,[method],tmp_path/'raw')
    if allowed:
        exclusions=module().day_exclusions(snapshot,source['shards'],reference_directory=tmp_path/'reference')
        assert len(exclusions)==1 and exclusions[0]['ts_code']==code
        assert source['shards'][0]['rows'][0]['ts_code']==code
    else:
        with pytest.raises(PreparationFailure,match='SOURCE_CONFLICT'):
            module().day_exclusions(snapshot,source['shards'],reference_directory=tmp_path/'reference')

@pytest.mark.parametrize('bad',['day','duplicate','price','schema','capacity','empty'])
def test_native_invalid_quote_response_is_retained_not_declared_complete(tmp_path,bad):
    rows=[quote()]
    if bad=='day':rows[0]['trade_date']='20260929'
    if bad=='duplicate':rows.append(dict(rows[0]))
    if bad=='price':rows[0]['low']=20
    if bad=='capacity':rows=[quote()]*6000
    if bad=='empty':rows=[]
    class Client:
        def daily(self,**params):
            frame=pd.DataFrame(rows,columns=DAILY)
            return frame.drop(columns='close') if bad=='schema' else frame
    acq=module().NativeDayAcquirer(lambda:Client(),lambda directory:Pacing(),clock=lambda:1790596800)
    with pytest.raises(ValueError):acq.collect('2026-09-28',['daily'],tmp_path)
    assert not (tmp_path/'complete.json').exists()

def test_raw_hash_tamper_is_not_reused(tmp_path):
    acq=module().NativeDayAcquirer(lambda:factor_client([dict(ts_code='000001.SZ',trade_date='20260928',adj_factor=1.0)],[]),lambda directory:Pacing(),clock=lambda:1790596800)
    acq.collect('2026-09-28',['adj_factor'],tmp_path)
    next(tmp_path.rglob('*.parquet')).write_bytes(b'bad')
    with pytest.raises(ValueError):acq.collect('2026-09-28',['adj_factor'],tmp_path)

def test_provider_bj_rows_remain_raw_but_not_target_evidence(tmp_path):
    from app.preparation.evidence import EvidenceStore
    from app.repositories.source_evidence_repository import SourceEvidenceRepository
    rows=[dict(ts_code=code,trade_date='20260928',adj_factor=1.0) for code in ('000001.SZ','920001.BJ')]
    acquired=module().NativeDayAcquirer(lambda:factor_client(rows,[]),lambda directory:Pacing(),clock=lambda:1790596800).collect('2026-09-28',['adj_factor'],tmp_path/'raw')
    snapshot=freeze_market('2026-09-28',[reference()],'a'*64,'2026-10-11T20:00:00+08:00')
    with database() as factory:
        evidence=EvidenceStore(tmp_path/'shared',SourceEvidenceRepository(factory))
        refs=module().register_day_evidence(evidence,snapshot,acquired['shards'])
        assert {r['ts_code'] for r in refs}=={'000001.SZ'}
        assert {r['ts_code'] for r in acquired['shards'][0]['rows']}=={'000001.SZ','920001.BJ'}
        assert evidence.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-28')['adj_factor'][0]['adj_factor']==1.0

def test_unknown_valid_a_share_cannot_be_silently_ignored(tmp_path):
    rows=[dict(ts_code='600000.SH',trade_date='20260928',adj_factor=1.0)]
    acquired=module().NativeDayAcquirer(lambda:factor_client(rows,[]),lambda directory:Pacing(),clock=lambda:1790596800).collect('2026-09-28',['adj_factor'],tmp_path)
    snapshot=freeze_market('2026-09-28',[reference()],'a'*64,'2026-10-11T20:00:00+08:00')
    from app.preparation.service import PreparationFailure
    with pytest.raises(PreparationFailure,match='SOURCE_CONFLICT'):module().register_day_evidence(None,snapshot,acquired['shards'])

def test_empty_suspension_proves_query_coverage_and_owner_fences_index(tmp_path):
    from app.preparation.evidence import EvidenceStore
    from app.repositories.source_evidence_repository import SourceEvidenceRepository
    class Client:
        def suspend_d(self,**params):return pd.DataFrame(columns=SUSPEND)
    acquired=module().NativeDayAcquirer(lambda:Client(),lambda directory:Pacing(),clock=lambda:1790596800).collect('2026-09-28',['suspend_d'],tmp_path/'raw')
    snapshot=freeze_market('2026-09-28',[reference()],'a'*64,'2026-10-11T20:00:00+08:00')
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        evidence=EvidenceStore(tmp_path/'shared',SourceEvidenceRepository(factory))
        guard=lambda session:repo.fence(task['task_id'],OWNER['token'],now=NOW,session=session)
        refs=module().register_day_evidence(evidence,snapshot,acquired['shards'],owner_guard=guard)
        assert evidence.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-28')['queried_suspensions']==[('000001.SZ','2026-09-28')]
        def lost(session):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        with pytest.raises(RuntimeError,match='OWNER'):module().register_day_evidence(evidence,snapshot,acquired['shards'],owner_guard=lost)

def test_basic_nulls_preserved_and_supplement_budget_is_bounded():
    raw=dict(ts_code='000001.SZ',trade_date='20260928',turnover_rate=1.0,pe=None,pe_ttm=None,pb=1.0,ps=2.0,total_mv=1000.0)
    assert module().validate_native_day('daily_basic','2026-09-28',[raw])[0]['pe'] is None
    result=module().supplemental_requests(dict(missing_daily=[],missing_basic=[['000001.SZ','2026-09-28'],['600000.SH','2026-09-28']]),max_requests=1)
    assert len(result['requests'])==1 and result['exhausted'] is True
    assert result['requests'][0]['query_codes']==['000001.SZ']

def test_network_retry_and_safe_status_use_existing_shared_requests(tmp_path):
    from app.preparation.acquisition import MarketAcquirer
    from app.preparation.service import PreparationFailure
    from app.market_data.checked_tushare_api import TushareRequestError
    ticks=[1790596800.0];calls=[]
    class Client:
        def daily(self,**params):calls.append(1);raise TushareRequestError('NETWORK',502)
    pacing=MarketAcquirer(lambda:Client(),tmp_path/'canonical/request_state.json',clock=lambda:ticks[0],sleep=lambda sec:ticks.__setitem__(0,ticks[0]+sec))
    acq=module().NativeDayAcquirer(lambda:Client(),pacing.requests,clock=lambda:ticks[0])
    with pytest.raises(PreparationFailure) as caught:acq.collect('2026-09-28',['daily'],tmp_path/'raw')
    assert len(calls)==3 and caught.value.kind=='NETWORK' and caught.value.http_status==502
    assert caught.value.__context__ is None

def test_expired_quota_then_busy_lock_uses_current_busy_deadline(tmp_path):
    from app.preparation.acquisition import MarketAcquirer
    from app.preparation.service import PreparationFailure
    from app.market_data.forward_snapshot import write
    state=tmp_path/'canonical/request_state.json';state.parent.mkdir();write(state,dict(failure='HOURLY_QUOTA',retry_not_before=10))
    state.with_suffix('.lock').write_text('foreign owner',encoding='utf-8')
    def forbidden():raise AssertionError('SDK_WHILE_LOCK_BUSY')
    pacing=MarketAcquirer(forbidden,state,clock=lambda:20)
    with pytest.raises(PreparationFailure) as caught:module().NativeDayAcquirer(forbidden,pacing.requests,clock=lambda:20).collect('2023-01-03',['daily'],tmp_path/'raw')
    assert caught.value.kind=='REQUEST_STATE_BUSY' and caught.value.retry_not_before==80

def test_missing_client_permission_stays_permission_without_retries(tmp_path):
    from app.preparation.acquisition import MarketAcquirer
    from app.preparation.service import PreparationFailure
    ticks=[1790596800.0];calls=[]
    def missing():calls.append(1);raise PreparationFailure('PERMISSION')
    pacing=MarketAcquirer(missing,tmp_path/'canonical/request_state.json',clock=lambda:ticks[0],sleep=lambda sec:ticks.__setitem__(0,ticks[0]+sec))
    with pytest.raises(PreparationFailure) as caught:module().NativeDayAcquirer(missing,pacing.requests,clock=lambda:ticks[0]).collect('2026-09-28',['daily'],tmp_path/'raw')
    assert caught.value.kind=='PERMISSION' and caught.value.__context__ is None
    assert len(calls)==1
