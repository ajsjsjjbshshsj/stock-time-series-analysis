from copy import deepcopy
from datetime import datetime
import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pandas as pd
import pytest
from sqlalchemy.exc import IntegrityError
from nightly_support import reference
from nightly_store_support import database,store,claim,NOW,OWNER
from preparation_market_support import fixture
from test_preparation_service import Market,Calendar
from app.nightly.contracts import freeze_market
from app.nightly.acquisition import NativeDayAcquirer
from app.preparation.evidence import EvidenceStore
from app.preparation.service import PreparationFailure
from app.repositories.source_evidence_repository import SourceEvidenceRepository
from app.repositories.market_repair_repository import MarketRepairRepository
from app.market_data.repair_contract import DAILY,BASIC,FACTOR,SUSPEND,canonical

DAY='2026-09-28'
def service_cls():return importlib.import_module('app.nightly.service').MarketUpdateService

class CheckedMarket(Market):
    def insert_batch(self,table,rows,schema):
        if self.owner_guard:self.owner_guard(None)
        inserted=equal=0;conflicts=[]
        for row in rows:
            existing=next((r for r in self.tables[table] if (r['ts_code'],r['trade_date'])==(row['ts_code'],row['trade_date'])),None)
            if existing is None:
                self.tables[table].append(deepcopy(row));inserted+=1
            elif canonical(existing)==canonical(row):equal+=1
            else:conflicts.append([row['ts_code'],row['trade_date']])
        self.writes+=inserted
        return dict(inserted=inserted,already_equal=equal,conflicts=conflicts)
    def verify(self,baseline,candidates):
        current=self.capture([r['ts_code'] for r in baseline['stock_basic']],baseline['start'],baseline['end'])
        MarketRepairRepository._assert_original(baseline,current)
        return dict(db_import_complete=True,original_rows_unchanged=True,missing=[],conflicts=[])

def make(factory,tmp_path,*,empty=False,conflict=False):
    pool,window,tables,evidence=fixture()
    expected=deepcopy(tables)
    for table in ('stock_daily','stock_daily_basic'):tables[table]=[r for r in tables[table] if r['trade_date']==DAY]
    if empty:tables['stock_daily']=[];tables['stock_daily_basic']=[]
    if conflict:tables['stock_daily']=[r for r in tables['stock_daily'] if r['ts_code']!='000001.SZ']
    market=CheckedMarket(tables);tasks=store(factory)
    source=EvidenceStore(tmp_path,SourceEvidenceRepository(factory))
    source.register_verified(dict(kind='adj_factor',provider='tushare.adj_factor',start_date=DAY,end_date=DAY,
        expected_codes=pool['codes'],rows=[r for r in evidence['adj_factor'] if r['trade_date']==DAY]))
    if conflict:
        source.register_verified(dict(kind='suspend_d',provider='tushare.suspend_d',start_date=DAY,end_date=DAY,expected_codes=['000001.SZ'],
            rows=[dict(ts_code='000001.SZ',trade_date='20260928',suspend_type=t,suspend_timing=None) for t in ('S','R')]))
    snapshot=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE')],'a'*64,DAY+'T20:00:00+08:00')
    class Universe:
        on_progress=None
        def freeze(self,day,directory):assert day==DAY;return deepcopy(snapshot)
    calls=[]
    class Pacing:
        def call(self,method,params):return method(**params)
    class Client:
        def respond(self,method,fields,params):
            calls.append(method)
            if method=='suspend_d':return pd.DataFrame(columns=SUSPEND)
            table={'daily':'stock_daily','daily_basic':'stock_daily_basic'}[method]
            records=[]
            for original in expected[table]:
                if original['trade_date']!=DAY:continue
                if params.get('ts_code') and original['ts_code'] not in params['ts_code'].split(','):continue
                row={k:(float(original[k]) if original[k] is not None and k not in ('ts_code','trade_date') else original[k]) for k in fields}
                row['trade_date']='20260928';records.append(row)
            return pd.DataFrame(records,columns=fields)
        def daily(self,**params):return self.respond('daily',DAILY,params)
        def daily_basic(self,**params):return self.respond('daily_basic',BASIC,params)
        def suspend_d(self,**params):return self.respond('suspend_d',SUSPEND,params)
    clock=lambda:datetime.fromisoformat(NOW).timestamp()
    acq=NativeDayAcquirer(lambda:Client(),lambda directory:Pacing(),clock=clock)
    service=service_cls()(tasks,market,source,Calendar(evidence['calendar']),Universe(),acq,None,tmp_path,clock=clock)
    return service,tasks,market,calls,snapshot

def execute(service,tasks):
    task=service.submit(DAY)
    return service.execute(claim(tasks),owner_token=OWNER['token'])

def test_complete_day_reuses_inventory_without_source_requests(tmp_path):
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path)
        result=execute(service,tasks)
        assert result['status']=='SUCCEEDED' and result['result']['data_ready'] is True
        assert result['result']['inserted_this_attempt']==0 and result['result']['model_ready'] is False
        assert calls==[] and market.writes==0

def test_new_period_service_seals_exclusion_audit_for_actual_registration(tmp_path):
    from app.nightly.universe import MarketUniverseProvider
    from app.repositories.market_update_repository import MarketUpdateRepository
    from app.nightly.contracts import FIELDS
    from app.nightly.service import sealed
    class Client:
        def stock_basic(self,**params):
            records=[reference(),reference('600000.SH',exchange='SSE')]
            return pd.DataFrame([r for r in records if r['exchange']==params['exchange'] and r['list_status']==params['list_status']],columns=FIELDS)
    class Pacing:
        def call(self,method,params):return method(**params)
    with database() as factory:
        service,_,market,calls,_=make(factory,tmp_path,empty=True)
        service.tasks=MarketUpdateRepository(factory,requirements_version='market-v2')
        service.universe_provider=MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=service.clock,schema_version=2)
        result=execute(service,service.tasks)
        assert result['status']=='SUCCEEDED'
        assert result['universe']['schema_version']==2
        audit=tmp_path/'market_updates'/result['task_id']/'attempts/000001/source_exclusions.json'
        assert sealed(audit)==[]

@pytest.mark.parametrize('register_at_verify',[False,True])
def test_v2_verified_legacy_precision_keeps_old_rows_and_seals_audit(tmp_path,register_at_verify):
    from app.nightly.universe import MarketUniverseProvider
    from app.repositories.market_update_repository import MarketUpdateRepository
    from app.nightly.contracts import FIELDS
    from app.nightly.service import sealed
    class Client:
        def stock_basic(self,**params):
            records=[reference(),reference('600000.SH',exchange='SSE')]
            return pd.DataFrame([r for r in records if r['exchange']==params['exchange'] and r['list_status']==params['list_status']],columns=FIELDS)
    class Pacing:
        def call(self,method,params):return method(**params)
    with database() as factory:
        service,_,market,calls,_=make(factory,tmp_path)
        service.tasks=MarketUpdateRepository(factory,requirements_version='market-v2')
        service.universe_provider=MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=service.clock,schema_version=2)
        old=market.tables['stock_daily'][0];old['amount']='90550.234375'
        raw={field:(old[field] if field in DAILY[:2] else float(old[field])) for field in DAILY};raw.update(trade_date='20260928',amount=90550.232)
        bundle=dict(kind='daily',provider='tushare.daily',start_date=DAY,end_date=DAY,expected_codes=['000001.SZ'],rows=[raw])
        if register_at_verify:
            stage=service._stage
            def concurrent_registration(task,token,phase,progress=None):
                if phase=='VERIFY':service.evidence_store.register_verified(bundle)
                return stage(task,token,phase,progress)
            service._stage=concurrent_registration
        else:service.evidence_store.register_verified(bundle)
        before=deepcopy(market.tables)
        result=execute(service,service.tasks)
        expected_count=0 if register_at_verify else 1
        assert result['status']=='SUCCEEDED' and result['result']['legacy_float32_compatible_count']==expected_count
        assert result['result']['inserted_this_attempt']==0 and market.tables==before
        audit=sealed(tmp_path/'market_updates'/result['task_id']/'attempts/000001/precision_compatibility.json')
        assert len(audit['rows'])==expected_count
        if not register_at_verify:assert audit['rows'][0]['changed_fields']==['amount']
        assert audit['source_manifest_sha256'] and len(audit['baseline_sha256'])==64

@pytest.mark.parametrize('interruption',['pointer_write','bound_pointer_missing'])
def test_reference_binding_recovers_original_source_without_poisoning_retry(tmp_path,monkeypatch,interruption):
    from app.nightly.universe import MarketUniverseProvider
    from app.repositories.market_update_repository import MarketUpdateRepository
    from app.nightly.contracts import FIELDS
    import app.nightly.service as implementation
    calls=[]
    class Client:
        def stock_basic(self,**params):
            calls.append(1);records=[reference(),reference('600000.SH',exchange='SSE')]
            return pd.DataFrame([r for r in records if r['exchange']==params['exchange'] and r['list_status']==params['list_status']],columns=FIELDS)
    class Pacing:
        def call(self,method,params):return method(**params)
    with database() as factory:
        service,_,market,_,_=make(factory,tmp_path)
        service.tasks=MarketUpdateRepository(factory,requirements_version='market-v2')
        service.universe_provider=MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=service.clock,schema_version=2)
        original=implementation.sealed
        def interrupted(path,value=None):
            if path.name=='reference_source.json':raise OSError('simulated interrupted pointer')
            return original(path,value)
        if interruption=='pointer_write':monkeypatch.setattr(implementation,'sealed',interrupted)
        else:
            # Simulate an already-bound legacy failure with its original source
            # present, but the source pointer missing. Do not delete any file.
            service.submit(DAY);claimed=claim(service.tasks)
            directory=tmp_path/'market_updates'/claimed['task_id']/'attempts/000001/reference'
            value=service.universe_provider.freeze(DAY,directory)
            service.tasks.bind_universe(claimed['task_id'],OWNER['token'],value,now=NOW)
            service.tasks.update(claimed['task_id'],OWNER['token'],dict(status='FAILED',error_kind='SCHEMA',updated_at=NOW))
        first=execute(service,service.tasks) if interruption=='pointer_write' else service.tasks.get(claimed['task_id'])
        assert first['status']=='FAILED'
        if interruption=='pointer_write':assert first['universe'] is None
        monkeypatch.setattr(implementation,'sealed',original)
        service.tasks.retry(first['task_id'],now=NOW)
        service.clock=lambda:datetime.fromisoformat(NOW).timestamp()+60
        service.universe_provider.clock=service.clock
        repeated=service.execute(claim(service.tasks),owner_token=OWNER['token'])
        assert repeated['status']=='SUCCEEDED'
        if interruption=='bound_pointer_missing':assert len(calls)==6

def test_empty_sql_fetches_bulk_quote_and_basic_then_inserts_only_missing_rows(tmp_path):
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path,empty=True)
        result=execute(service,tasks)
        assert result['status']=='SUCCEEDED' and result['result']['data_ready']
        assert result['result']['inserted_this_attempt']==4 and market.writes==4
        assert calls.count('daily')==1 and calls.count('daily_basic')==1

def test_preexisting_ambiguous_suspension_is_partial_and_never_fabricated(tmp_path):
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path,conflict=True)
        result=execute(service,tasks)
        assert result['status']=='PARTIAL' and result['result']['data_ready'] is False
        assert result['result']['issues']['SOURCE_CONFLICT']==1 and market.writes==0

def test_click_before_20_or_closed_day_cannot_submit(tmp_path):
    with database() as factory:
        service,tasks,_,_,_=make(factory,tmp_path)
        service.clock=lambda:datetime.fromisoformat('2026-09-28T11:59:59Z').timestamp()
        with pytest.raises(ValueError):service.submit(DAY)
        service.clock=lambda:datetime.fromisoformat('2026-09-28T12:00:00Z').timestamp()
        service.calendar_store=Calendar(dict(source='tushare.trade_cal',rows=[dict(cal_date=DAY,is_open=0)]))
        with pytest.raises(ValueError):service.submit(DAY)
        assert tasks.earliest_date() is None

def test_lost_owner_cannot_import_or_publish(tmp_path):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path)
        service.submit(DAY);task=claim(tasks)
        with pytest.raises(RuntimeError,match='OWNER'):service.execute(task,owner_token='2'*32)
        assert tasks.get(task['task_id'])['status']=='RUNNING' and market.writes==0

def test_nonduplicate_sql_error_is_persisted_failed_without_private_diagnostics(tmp_path):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path,empty=True)
        def failure(*args,**kwargs):raise IntegrityError('private SQL',{'token':'private'},Exception('password'))
        market.insert_batch=failure
        result=execute(service,tasks)
        assert result['status']=='FAILED' and result['error_kind']=='TRANSACTION_FAILED'
        assert 'private' not in str(result) and market.writes==0

def test_source_quota_persists_future_wait_not_tight_loop(tmp_path):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path,empty=True)
        class Quota:
            on_progress=None
            def collect(self,*args,**kwargs):raise PreparationFailure('HOURLY_QUOTA',retry_not_before=10)
        service.acquirer=Quota();result=execute(service,tasks)
        assert result['status']=='WAITING_RETRY'
        assert datetime.fromisoformat(result['retry_not_before']).timestamp()==service.clock()+60
        assert market.writes==0

def test_retry_detects_original_sql_row_change_instead_of_replacing_baseline(tmp_path):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path,conflict=True)
        result=execute(service,tasks);assert result['status']=='PARTIAL'
        market.tables['stock_daily'][0]['close']='10.7'
        tasks.retry(result['task_id'],now=NOW)
        repeated=service.execute(claim(tasks),owner_token=OWNER['token'])
        assert repeated['status']=='FAILED' and repeated['error_kind']=='ORIGINAL_CHANGED'
        assert market.writes==0

def test_reference_with_future_acquired_timestamp_cannot_be_bound(tmp_path):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path)
        bad=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE')],'b'*64,'2026-10-11T20:00:00+08:00')
        class Future:
            def freeze(self,day,directory):return bad
        service.universe_provider=Future()
        result=execute(service,tasks)
        assert result['status']=='FAILED' and result['error_kind']=='SCHEMA'
        assert result['universe'] is None and market.writes==0

def test_missing_reference_is_inserted_from_verified_snapshot_only(tmp_path):
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path)
        original=market.tables['stock_basic'].pop(0)
        class Reference:
            def ensure_from_snapshot(self,snapshot,codes,*,owner_guard):
                assert codes==['000001.SZ'];owner_guard(None)
                market.tables['stock_basic'].append(original)
        service.reference_loader=Reference()
        result=execute(service,tasks)
        assert result['status']=='SUCCEEDED' and calls==[] and market.writes==0

def test_commit_before_receipt_recovery_reuses_source_without_duplicate_insert(tmp_path,monkeypatch):
    from app.preparation.importer import FileReceiptStore
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path,empty=True)
        save=FileReceiptStore.save;once=[False]
        def crash(self,data):
            if data['batches'] and not once[0]:once[0]=True;raise OSError('receipt interruption')
            return save(self,data)
        monkeypatch.setattr(FileReceiptStore,'save',crash)
        first=execute(service,tasks)
        assert first['status']=='FAILED' and market.writes>0
        # Simulate lease recovery in the same attempt, not a fresh manual retry.
        from sqlalchemy import text
        with factory.begin() as session:
            session.execute(text("UPDATE market_update_run SET status='QUEUED',error_kind=NULL WHERE task_id=:id"),dict(id=first['task_id']))
        monkeypatch.setattr(FileReceiptStore,'save',save)
        before_calls=len(calls)
        resumed=service.execute(claim(tasks),owner_token=OWNER['token'])
        assert resumed['status']=='SUCCEEDED' and market.writes==4
        assert resumed['result']['already_equal']>0 and len(calls)==before_calls

def test_snapshot_reference_writer_does_not_overwrite_existing_info_or_accept_bj(tmp_path):
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    module=importlib.import_module('app.nightly.reference')
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
        conn.execute(text("INSERT INTO stock_basic VALUES ('000001.SZ','000001','保留名称','深圳','银行','1991-04-03')"))
    snapshot=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE',list_date='19991110')],'a'*64,DAY+'T20:00:00+08:00')
    writer=module.SnapshotReferenceWriter(sessionmaker(bind=engine))
    try:
        writer.ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lambda session:None)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM stock_basic WHERE ts_code='000001.SZ'")).scalar()=='保留名称'
            assert str(conn.execute(text("SELECT list_date FROM stock_basic WHERE ts_code='600000.SH'")).scalar())=='1999-11-10'
        with pytest.raises(ValueError):writer.ensure_from_snapshot(snapshot,['920001.BJ'],owner_guard=lambda session:None)
    finally:engine.dispose()

def test_reference_writer_rolls_back_new_rows_when_existing_ipo_conflicts():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from app.nightly.reference import SnapshotReferenceWriter
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
        conn.execute(text("INSERT INTO stock_basic VALUES ('600000.SH','600000','旧名称',NULL,NULL,'1999-11-10')"))
    snapshot=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE')],'a'*64,DAY+'T20:00:00+08:00')
    writer=SnapshotReferenceWriter(sessionmaker(bind=engine))
    try:
        with pytest.raises(PreparationFailure,match='INVALID_EXISTING_DATA'):
            writer.ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lambda session:None)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_basic')).scalar()==1
    finally:engine.dispose()

def test_reference_owner_fence_prevents_all_reference_writes():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from app.nightly.reference import SnapshotReferenceWriter
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
    snapshot=freeze_market(DAY,[reference()],'a'*64,DAY+'T20:00:00+08:00')
    def lost(session):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
    try:
        with pytest.raises(RuntimeError,match='OWNER'):
            SnapshotReferenceWriter(sessionmaker(bind=engine)).ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lost)
        with engine.connect() as conn:assert conn.execute(text('SELECT COUNT(*) FROM stock_basic')).scalar()==0
    finally:engine.dispose()

def test_tampered_snapshot_is_rejected_before_binding_or_writes(tmp_path):
    with database() as factory:
        service,tasks,market,_,snapshot=make(factory,tmp_path)
        snapshot['codes'].append('920001.BJ')
        class Bad:
            def freeze(self,day,directory):return snapshot
        service.universe_provider=Bad()
        result=execute(service,tasks)
        assert result['status']=='FAILED' and result['error_kind']=='SCHEMA'
        assert result['universe'] is None and market.writes==0

def test_supplement_budget_exhaustion_never_marks_missing_quotes_ready(tmp_path,monkeypatch):
    with database() as factory:
        service,tasks,market,_,_=make(factory,tmp_path,empty=True)
        create=service.acquirer.client_factory
        class Incomplete:
            def __init__(self):self.client=create()
            def daily(self,**params):return self.client.daily(**params).iloc[:1]
            def daily_basic(self,**params):return self.client.daily_basic(**params).iloc[:1]
            def suspend_d(self,**params):return self.client.suspend_d(**params)
        service.acquirer.client_factory=Incomplete
        monkeypatch.setattr(importlib.import_module('app.nightly.service'),'supplemental_requests',
            lambda inventory:dict(requests=[],exhausted=True,unplanned_count=1))
        result=execute(service,tasks)
        assert result['status']=='PARTIAL' and not result['result']['data_ready']
        assert result['result']['supplement_budget_exhausted'] and market.writes==2

@pytest.mark.parametrize('code,day',[('920001.BJ',DAY),('000001.SZ','2022-12-30')])
def test_importer_rejects_candidates_outside_market_baseline(tmp_path,code,day):
    from app.preparation.importer import insert_candidates,FileReceiptStore
    with database() as factory:
        service,tasks,market,_,snapshot=make(factory,tmp_path)
        baseline=market.capture(snapshot['codes'],DAY,DAY)
        row=dict(baseline['stock_daily'][0],ts_code=code,trade_date=day)
        with pytest.raises(ValueError,match='CANDIDATE_OUTSIDE_FROZEN_BASELINE'):
            insert_candidates(market,baseline,dict(stock_daily=[row]),FileReceiptStore(tmp_path/'reject'),lambda progress:None)
        assert market.writes==0
