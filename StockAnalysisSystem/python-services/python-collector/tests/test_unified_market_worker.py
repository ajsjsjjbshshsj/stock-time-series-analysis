from pathlib import Path
from types import SimpleNamespace
from datetime import datetime
import importlib,sys,time
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from test_preparation_worker import setup,Service,NOW
from nightly_store_support import database,store
from app.preparation.worker import PreparationWorker

def queues():return importlib.import_module('app.preparation.worker').WorkerQueue

def test_two_real_queues_round_robin_and_write_to_correct_repository(tmp_path):
    with database() as factory:
        pools,p=setup(factory);market=store(factory)
        m=market.submit('2026-09-28',scheduled_for='2026-09-28T20:00:00+08:00',now='2026-10-11T09:00:00Z')
        original=pools.get(p['task_id'])['request']
        second=pools.submit(dict(original,requested_end='2026-09-29',cutoff_date='2026-09-29'),now='2026-10-11T09:01:00Z')
        ps=Service(pools,tmp_path);ms=Service(market,tmp_path);ticks=[]
        worker=PreparationWorker(pools,ps,clock=NOW,before_poll=lambda:ticks.append(1),queues=[queues()('market',market,ms)])
        with worker.maintenance_lock():
            one=worker._run_once();two=worker._run_once();three=worker._run_once();assert worker._run_once() is None
        assert [one['task_id'],two['task_id']]==[p['task_id'],m['task_id']]
        assert three['task_id']==second['task_id']
        assert ps.calls==[p['task_id'],second['task_id']] and ms.calls==[m['task_id']] and len(ticks)==4

def test_only_market_queue_ready_is_processed(tmp_path):
    with database() as factory:
        pools,p=setup(factory);market=store(factory)
        # Existing preparation run is terminal; the other queue must still run.
        from sqlalchemy import text
        with factory.begin() as session:session.execute(text("UPDATE data_preparation_task SET status='FAILED'"))
        m=market.submit('2026-09-28',scheduled_for='2026-09-28T20:00:00+08:00',now='2026-10-11T09:00:00Z')
        ps=Service(pools,tmp_path);ms=Service(market,tmp_path)
        result=PreparationWorker(pools,ps,clock=NOW,queues=[queues()('market',market,ms)]).run_once()
        assert result['task_id']==m['task_id'] and ps.calls==[]

def test_unknown_expired_market_owner_stops_both_queues(tmp_path):
    with database() as factory:
        pools,p=setup(factory);market=store(factory)
        market.submit('2026-09-28',scheduled_for='2026-09-28T20:00:00+08:00',now='2026-10-11T09:00:00Z')
        market.claim(dict(token='a'*32,pid=101,started_at='2026-10-11T09:00:00Z',host='foreign'),
            now='2026-10-11T09:00:00Z',lease_until='2026-10-11T09:02:00Z')
        ps=Service(pools,tmp_path);ms=Service(market,tmp_path)
        worker=PreparationWorker(pools,ps,clock=NOW,owner_probe=lambda owner:None,queues=[queues()('market',market,ms)])
        with pytest.raises(RuntimeError,match='RECOVERY_CONFIRMATION_REQUIRED'):worker.run_once()
        assert ps.calls==ms.calls==[]

def test_same_root_second_worker_cannot_acquire_maintenance_lock(tmp_path):
    class Repo:pass
    a=PreparationWorker(Repo(),SimpleNamespace(data_root=tmp_path));b=PreparationWorker(Repo(),SimpleNamespace(data_root=tmp_path),owner_probe=lambda owner:True)
    with a.maintenance_lock():
        with pytest.raises(RuntimeError,match='WORKER_BUSY'):
            with b.maintenance_lock():pytest.fail('second worker acquired active root')

def test_extra_queue_with_other_root_is_rejected(tmp_path):
    with pytest.raises(ValueError,match='QUEUE_ROOT_MISMATCH'):
        PreparationWorker(object(),SimpleNamespace(data_root=tmp_path),queues=[queues()('market',object(),SimpleNamespace(data_root=tmp_path/'other'))])

@pytest.mark.parametrize('invalid', ['early','bj'])
def test_nightly_policy_rejects_old_pool_task_without_trimming_members(tmp_path,invalid):
    module=importlib.import_module('app.nightly.scheduler')
    with database() as factory:
        tasks,p=setup(factory);wrapped=Service(tasks,tmp_path);task=tasks.get(p['task_id'])
        if invalid=='early':
            from sqlalchemy import text
            with factory.begin() as session:session.execute(text("UPDATE data_preparation_task SET status='FAILED'"))
            tasks.submit(dict(task['request'],requested_start='2022-01-01'),now='2026-10-11T09:00:00Z')
        class Pools:
            def resolve(self,**kwargs):return dict(codes=['000001.SZ','920001.BJ'] if invalid=='bj' else ['000001.SZ'])
        wrapped.pools=Pools();wrapped.clock=NOW
        policy=module.PolicyBoundPreparationService(wrapped)
        claimed=tasks.claim(dict(token='a'*32,pid=101,started_at='2026-10-11T09:00:00Z',host='local'),now='2026-10-11T09:03:00Z',lease_until='2026-10-11T09:05:00Z')
        result=policy.execute(claimed,owner_token='a'*32)
        assert result['status']=='FAILED' and result['result']['issues']=={'POLICY_SCOPE_VIOLATION':1}
        assert wrapped.calls==[]

@pytest.mark.parametrize('lose_owner',[False,True])
def test_real_file_sqlite_market_heartbeat_and_owner_loss(tmp_path,lose_owner):
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from nightly_store_support import SQLITE_DDL
    engine=create_engine('sqlite:///'+str(tmp_path/'tasks.sqlite'),connect_args={'check_same_thread':False})
    with engine.begin() as connection:connection.execute(text(SQLITE_DDL))
    repository=store(sessionmaker(bind=engine));beats=[]
    repository.submit('2026-09-28',scheduled_for='2026-09-28T20:00:00+08:00',now='2026-10-11T09:00:00Z')
    heartbeat=repository.heartbeat
    def track(*args,**kwargs):
        result=heartbeat(*args,**kwargs);beats.append(result);return result
    repository.heartbeat=track
    class Empty:
        def expired(self,**kwargs):return []
        def claim(self,*args,**kwargs):return None
    class Slow:
        data_root=tmp_path
        def execute(self,task,*,owner_token):
            time.sleep(.08)
            if lose_owner:
                with engine.begin() as connection:
                    connection.execute(text("UPDATE market_update_run SET owner_token=:token WHERE task_id=:id"),dict(token='b'*32,id=task['task_id']))
                time.sleep(.04)
            repository.fence(task['task_id'],owner_token,now='2026-10-11T09:03:00Z')
            assert repository.update(task['task_id'],owner_token,dict(status='SUCCEEDED',updated_at='2026-10-11T09:03:00Z'))
            return repository.get(task['task_id'])
    worker=PreparationWorker(Empty(),SimpleNamespace(data_root=tmp_path),clock=NOW,heartbeat_interval=.01,
        queues=[queues()('market',repository,Slow())])
    try:
        if lose_owner:
            with pytest.raises(RuntimeError,match='OWNER'):worker.run_once()
            assert False in beats
        # A renew racing with the terminal commit correctly returns False.
        else:assert worker.run_once()['status']=='SUCCEEDED' and beats.count(True)>=2
    finally:engine.dispose()

def test_slow_pool_job_crossing_20_enqueues_market_day_on_next_poll(tmp_path):
    from test_nightly_scheduler import make
    with database() as factory:
        pools,p=setup(factory);market=store(factory)
        ticks=[datetime.fromisoformat('2026-10-12T11:59:59Z').timestamp()]
        scheduler=make(market,tmp_path,'2026-10-12T11:59:59Z',bootstrap_start='2026-10-12');scheduler.clock=lambda:ticks[0]
        scheduler.calendar_provider=lambda start,end,now:dict(source='tushare.trade_cal',rows=[dict(cal_date='2026-10-12',is_open=1)])
        class Slow(Service):
            def execute(self,task,*,owner_token):
                ticks[0]+=2
                assert self.tasks.update(task['task_id'],owner_token,dict(status='SUCCEEDED',updated_at=datetime.fromtimestamp(ticks[0]).astimezone().isoformat()))
                return self.tasks.get(task['task_id'])
        class MarketService(Service):
            def execute(self,task,*,owner_token):
                assert self.tasks.update(task['task_id'],owner_token,dict(status='SUCCEEDED',updated_at=datetime.fromtimestamp(ticks[0]).astimezone().isoformat()))
                return self.tasks.get(task['task_id'])
        worker=PreparationWorker(pools,Slow(pools,tmp_path),clock=lambda:ticks[0],before_poll=scheduler.tick,
            queues=[queues()('market',market,MarketService(market,tmp_path))])
        with worker.maintenance_lock():
            assert worker._run_once()['task_id']==p['task_id']
            assert market.earliest_date() is None
            assert worker._run_once()['trade_date']=='2026-10-12'

def test_idle_worker_observes_next_boundary_with_one_second_sleep(tmp_path):
    from test_nightly_scheduler import make
    with database() as factory:
        market=store(factory);scheduler=make(market,tmp_path,'2026-09-28T11:59:59Z',bootstrap_start='2026-09-28')
        class Empty:
            def expired(self,**kwargs):return []
            def claim(self,*args,**kwargs):return None
            def next_retry(self):return None
        waits=[]
        worker=PreparationWorker(Empty(),SimpleNamespace(data_root=tmp_path),clock=scheduler.clock,before_poll=scheduler.tick,
            queues=[queues()('market',market,SimpleNamespace(data_root=tmp_path))])
        def sleep(delay):waits.append(delay);worker.stop()
        worker.sleep=sleep;worker.run_forever();assert waits==[1]
