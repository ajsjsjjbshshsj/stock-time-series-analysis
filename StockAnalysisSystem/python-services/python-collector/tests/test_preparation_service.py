from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

import importlib
import pytest
from preparation_store_support import database,load,request,OWNER
from preparation_market_support import fixture


def service_cls():
    try: return importlib.import_module('app.preparation.service').DataPreparationService
    except ModuleNotFoundError: pytest.fail('preparation service not implemented')


class Market:
    def __init__(self,tables): self.tables=deepcopy(tables); self.writes=0; self.owner_guard=None
    def capture(self,codes,start,end):
        result=deepcopy(self.tables); result.update(start=start,end=end)
        return result
    def insert_batch(self,table,rows,schema):
        if self.owner_guard: self.owner_guard(None)
        self.writes+=len(rows)
        self.tables[table].extend(deepcopy(rows))
        return dict(inserted=len(rows),already_equal=0,conflicts=[])
    def verify(self,baseline,candidates):
        return dict(db_import_complete=True,original_rows_unchanged=True,missing=[],conflicts=[])


class Calendar:
    def __init__(self,data): self.data=data
    def get(self,start,end,now,*,acquire=False): return deepcopy(self.data)


class Acquirer:
    def __init__(self): self.calls=[]
    def collect(self,requests,directory):
        self.calls.extend(requests)
        assert requests==[], 'complete database must not request market data'
        return dict(complete=True,shards=[])


def make(factory,tmp_path,*,conflict=False,missing_basic=False):
    pool,window,tables,evidence=fixture()
    if conflict:
        tables['stock_daily']=tables['stock_daily'][1:]
        evidence['suspend_d']=[dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type=t,suspend_timing=None) for t in ('S','R')]
    if missing_basic: tables['stock_daily_basic']=tables['stock_daily_basic'][1:]
    from app.preparation.evidence import EvidenceStore
    pools=load('pool_repository','PoolRepository')(factory); pools.register(pool,activate=True)
    tasks=load('preparation_repository','PreparationRepository')(factory)
    store=EvidenceStore(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
    for method in ('adj_factor','suspend_d'):
        store.register_verified(dict(kind=method,provider='tushare.'+method,start_date=window['start'],
            end_date=window['end'],expected_codes=pool['codes'],rows=evidence[method]))
    market=Market(tables); acquirer=Acquirer()
    now=lambda:datetime.fromisoformat('2026-10-11T09:00:00+00:00').timestamp()
    service=service_cls()(pools,tasks,market,store,Calendar(evidence['calendar']),acquirer,tmp_path,clock=now)
    return service,pools,tasks,market,acquirer,request(pool['pool_version'])


def claim(tasks,task):
    return tasks.claim(OWNER,now='2026-10-11T09:00:00Z',lease_until='2026-10-11T09:02:00Z')


def test_complete_shared_database_has_no_sdk_request_or_market_write(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,market,acquirer,req=make(factory,tmp_path)
        inspected=service.inspect(req)
        assert inspected['data_ready'] is True
        task=service.submit(req); claimed=claim(tasks,task)
        result=service.execute(claimed,owner_token=OWNER['token'])
        assert result['status']=='SUCCEEDED'
        assert result['result']['data_ready'] is True
        assert market.writes==0 and acquirer.calls==[]


def test_existing_source_conflict_is_partial_not_success(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,market,_,req=make(factory,tmp_path,conflict=True)
        task=service.submit(req)
        result=service.execute(claim(tasks,task),owner_token=OWNER['token'])
        assert result['status']=='PARTIAL'
        assert result['result']['data_ready'] is False
        assert result['result']['issues']['SOURCE_CONFLICT']==1
        assert market.writes==0


def test_queue_rechecks_data_filled_by_another_pool_before_execution(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,market,acquirer,req=make(factory,tmp_path,missing_basic=True)
        task=service.submit(req)
        market.tables['stock_daily_basic']=fixture()[2]['stock_daily_basic']
        result=service.execute(claim(tasks,task),owner_token=OWNER['token'])
        assert result['status']=='SUCCEEDED'
        assert market.writes==0 and acquirer.calls==[]


def test_lost_owner_cannot_publish_or_insert(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,market,_,req=make(factory,tmp_path)
        task=service.submit(req); claimed=claim(tasks,task)
        with pytest.raises(RuntimeError,match='OWNER'):
            service.execute(claimed,owner_token='2'*32)
        assert tasks.get(task['task_id'])['status']=='RUNNING'
        assert market.writes==0


def test_click_cutoff_bound_to_submission_not_worker_next_day(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,_,_,req=make(factory,tmp_path)
        service.clock=lambda:datetime.fromisoformat('2026-10-09T07:59:00+00:00').timestamp()
        req=dict(req,requested_end='2026-10-12')
        task=service.submit(req)
        assert task['request']['cutoff_date']=='2026-10-08'
        service.clock=lambda:datetime.fromisoformat('2026-10-09T08:01:00+00:00').timestamp()
        next_task=service.submit(req)
        assert next_task['task_id']!=task['task_id']
        assert next_task['request']['cutoff_date']=='2026-10-09'


def test_market_acquirer_uses_three_network_attempts_and_safe_status(tmp_path):
    from app.market_data.checked_tushare_api import TushareRequestError
    from app.market_data.repair_contract import request as market_request
    try: module=importlib.import_module('app.preparation.acquisition')
    except ModuleNotFoundError: pytest.fail('checked preparation acquirer not implemented')
    from app.preparation.service import PreparationFailure
    attempts=[]; ticks=[1000.0]
    class Client:
        def daily(self,**params):
            attempts.append(1)
            raise TushareRequestError('NETWORK',http_status=502)
    acq=module.MarketAcquirer(lambda:Client(),tmp_path/'shared/request_state.json',
        clock=lambda:ticks[0],sleep=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    with pytest.raises(PreparationFailure) as caught:
        acq.collect([market_request('daily',['000001.SZ'],'2026-09-28','2026-09-28')],tmp_path/'run')
    assert len(attempts)==3
    assert caught.value.kind=='NETWORK'
    assert caught.value.http_status==502


def test_market_acquirer_complete_shards_resume_without_initializing_sdk(tmp_path):
    import pandas as pd
    from app.market_data.repair_contract import request as market_request
    try: module=importlib.import_module('app.preparation.acquisition')
    except ModuleNotFoundError: pytest.fail('checked preparation acquirer not implemented')
    rows=fixture()[2]['stock_daily'][0]
    class Client:
        def daily(self,**params):
            data={k:(float(rows[k]) if k not in ('ts_code','trade_date') else rows[k]) for k in params['fields'].split(',')}
            data['trade_date']='20260928'
            return pd.DataFrame([data])
    requests=[market_request('daily',['000001.SZ'],'2026-09-28','2026-09-28')]
    module.MarketAcquirer(lambda:Client(),tmp_path/'shared/request_state.json').collect(requests,tmp_path/'run')
    def forbidden(): pytest.fail('complete source incorrectly initializes SDK')
    completed=module.MarketAcquirer(forbidden,tmp_path/'shared/request_state.json').collect(requests,tmp_path/'run')
    assert completed['complete'] is True
    assert completed['shards'][0]['row_count']==1


def test_cached_calendar_does_not_require_market_token(tmp_path):
    try: module=importlib.import_module('app.preparation.calendar')
    except ModuleNotFoundError: pytest.fail('calendar store not implemented')
    typ=getattr(module,'CalendarStore',None)
    assert typ is not None,'durable calendar store not implemented'
    def forbidden(*args): pytest.fail('cached historical calendar initializes market source')
    store=typ(tmp_path,forbidden,forbidden)
    store.register(fixture()[3]['calendar'])
    data=store.get('2026-09-28','2026-09-30',datetime.fromisoformat('2026-10-11T09:00:00Z'),acquire=True)
    assert data['source']=='tushare.trade_cal'
    assert len(data['rows'])==3


def test_quota_error_becomes_persisted_wait_with_same_deadline(tmp_path):
    service_cls()
    from app.preparation.service import PreparationFailure
    with database() as factory:
        service,_,tasks,market,_,req=make(factory,tmp_path,missing_basic=True)
        class Quota:
            def collect(self,requests,directory):
                raise PreparationFailure('HOURLY_QUOTA',retry_not_before=service.clock()+3601)
        service.acquirer=Quota()
        task=service.submit(req)
        result=service.execute(claim(tasks,task),owner_token=OWNER['token'])
        assert result['status']=='WAITING_RETRY'
        assert datetime.fromisoformat(result['retry_not_before']).timestamp()==service.clock()+3601
        assert market.writes==0


def test_missing_reference_is_loaded_only_when_needed(tmp_path):
    service_cls()
    with database() as factory:
        service,_,tasks,market,_,req=make(factory,tmp_path)
        stock=market.tables['stock_basic'].pop(0); requested=[]
        class Reference:
            def ensure(self,pool,codes,directory):
                requested.extend(codes); market.tables['stock_basic'].append(stock)
        service.reference_loader=Reference()
        task=service.submit(req)
        result=service.execute(claim(tasks,task),owner_token=OWNER['token'])
        assert result['status']=='SUCCEEDED'
        assert requested==['000001.SZ']


def test_reference_loader_does_not_overwrite_existing_stock_info(tmp_path):
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    import pandas as pd
    try: module=importlib.import_module('app.preparation.reference')
    except ModuleNotFoundError: pytest.fail('missing-reference loader not implemented')
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic (ts_code TEXT UNIQUE,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
        conn.execute(text("INSERT INTO stock_basic VALUES ('000001.SZ','000001','保留名称','深圳','银行','1991-04-03')"))
    class Pacing:
        def call(self,method,params): return method(**params)
    class Client:
        def stock_basic(self,**params):
            return pd.DataFrame([dict(ts_code='000001.SZ',symbol='000001',name='不应覆盖',area='深圳',industry='银行',list_date='19910403'),
                dict(ts_code='600000.SH',symbol='600000',name='新股票',area='上海',industry='银行',list_date='19991110')])
    loader=module.ReferenceLoader(lambda:Client(),lambda directory:Pacing(),sessionmaker(bind=engine))
    try:
        loader.ensure(fixture()[0],['000001.SZ','600000.SH'],tmp_path)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM stock_basic WHERE ts_code='000001.SZ'")).scalar()=='保留名称'
            assert conn.execute(text("SELECT list_date FROM stock_basic WHERE ts_code='600000.SH'")).scalar()=='1999-11-10'
    finally: engine.dispose()


def test_acquisition_progress_is_persisted_per_completed_request(tmp_path):
    import pandas as pd
    from app.market_data.repair_contract import request as market_request
    from app.preparation.acquisition import MarketAcquirer
    rows=fixture()[2]['stock_daily'][0]
    class Client:
        def daily(self,**params):
            data={k:(float(rows[k]) if k not in ('ts_code','trade_date') else rows[k]) for k in params['fields'].split(',')}
            data['trade_date']=params['trade_date']
            return pd.DataFrame([data])
    ticks=[1000.0]; progress=[]
    acq=MarketAcquirer(lambda:Client(),tmp_path/'shared/request_state.json',clock=lambda:ticks[0],
        sleep=lambda seconds:ticks.__setitem__(0,ticks[0]+seconds))
    acq.on_progress=lambda done,total:progress.append((done,total))
    requests=[market_request('daily',['000001.SZ'],day,day) for day in ('2026-09-28','2026-09-29')]
    acq.collect(requests,tmp_path/'run')
    assert progress==[(1,2),(2,2)]


def test_tampered_saved_plan_outside_pool_is_refused_without_sdk(tmp_path):
    import json
    from app.market_data.forward_snapshot import write,digest
    from app.market_data.repair_contract import request as market_request
    with database() as factory:
        service,_,tasks,market,acq,req=make(factory,tmp_path)
        task=service.submit(req); claimed=claim(tasks,task)
        directory=tmp_path/'tasks'/task['task_id']; directory.mkdir(parents=True)
        path=directory/'requests.json'
        write(path,[market_request('daily',['999999.SZ'],'2026-09-28','2026-09-28')])
        write(path.with_suffix('.proof.json'),dict(sha256=digest(path)))
        result=service.execute(claimed,owner_token=OWNER['token'])
        assert result['status']=='FAILED'
        assert result['error_kind']=='SCHEMA'
        assert market.writes==0 and acq.calls==[]


def test_full_market_basic_response_reusable_by_stock_outside_requesting_pool(tmp_path):
    from app.market_data.repair_contract import request as market_request,BASIC
    with database() as factory:
        service,_,_,_,_,_=make(factory,tmp_path)
        row=fixture()[2]['stock_daily_basic'][0]
        raw={k:(float(row[k]) if row[k] is not None and k not in ('ts_code','trade_date') else row[k]) for k in BASIC}
        raw.update(ts_code='600001.SH',trade_date='20260928')
        service._register(dict(shards=[dict(request=market_request('daily_basic',['000001.SZ'],'2026-09-28','2026-09-28'),rows=[raw])]),fixture()[0])
        refs=service.evidence_store.repository.list('daily_basic',['600001.SH'],'2026-09-28','2026-09-28')
        assert len(refs)==1
        data=service.evidence_store.load(refs,codes=['600001.SH'],start='2026-09-28',end='2026-09-28')
        assert data['daily_basic'][0]['ts_code']=='600001.SH'
