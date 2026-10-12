"""Real SQL queue and durable controller tests; only calendar transport is fake."""
from datetime import datetime,timedelta,date
from pathlib import Path
from types import SimpleNamespace
import importlib,sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from sqlalchemy import text
from nightly_store_support import database,store
from app.preparation.worker import PreparationWorker
from app.preparation.service import PreparationFailure


def clock(value):return lambda:datetime.fromisoformat(value).timestamp()

def provider(start,end,now):
    first=date.fromisoformat(start);last=date.fromisoformat(end)
    return dict(source='tushare.trade_cal',rows=[dict(cal_date=(first+timedelta(days=i)).isoformat(),
        is_open=int((first+timedelta(days=i)).isoformat() in {'2026-09-28','2026-09-29','2026-09-30'}))
        for i in range((last-first).days+1)])

def make(repo,root,now='2026-09-28T12:00:00Z',**kwargs):
    cls=importlib.import_module('app.nightly.scheduler').NightlyScheduler
    return cls(repo,provider,root,clock=clock(now),**kwargs)

def lock(repo,root):return PreparationWorker(repo,SimpleNamespace(data_root=root)).maintenance_lock()

def test_restart_enqueues_missing_days_not_just_global_max(tmp_path):
    with database() as factory:
        repo=store(factory)
        with lock(repo,tmp_path):make(repo,tmp_path,bootstrap_start='2026-09-28').tick()
        with lock(repo,tmp_path):
            restarted=make(repo,tmp_path,'2026-10-01T12:00:00Z');restarted.tick()
            runs=repo.list_runs('2026-09-28','2026-10-01')
            assert [r['trade_date'] for r in runs]==['2026-09-28','2026-09-29','2026-09-30']
            ids=[r['task_id'] for r in runs];restarted.tick()
            assert [r['task_id'] for r in repo.list_runs('2026-09-28','2026-10-01')]==ids

def test_first_default_only_enqueues_latest_due_session(tmp_path):
    with database() as factory:
        repo=store(factory)
        with lock(repo,tmp_path):make(repo,tmp_path,'2026-10-01T12:00:00Z').tick()
        assert [r['trade_date'] for r in repo.list_runs('2023-01-01','2026-10-01')]==['2026-09-30']

def test_before_boundary_has_no_today_task_and_one_second_delay(tmp_path):
    with database() as factory:
        repo=store(factory);scheduler=make(repo,tmp_path,'2026-09-28T11:59:59Z',bootstrap_start='2026-09-28')
        with lock(repo,tmp_path):scheduler.tick()
        assert repo.earliest_date() is None and scheduler.next_delay()==1

def test_failed_day_is_not_recreated_and_continuity_stops_at_it(tmp_path):
    with database() as factory:
        repo=store(factory);scheduler=make(repo,tmp_path,'2026-10-01T12:00:00Z',bootstrap_start='2026-09-28')
        with lock(repo,tmp_path):scheduler.tick()
        with factory.begin() as session:
            session.execute(text("UPDATE market_update_run SET status=CASE WHEN trade_date='2026-09-29' THEN 'FAILED' ELSE 'SUCCEEDED' END"))
        old=repo.list_runs('2026-09-28','2026-09-30')
        with lock(repo,tmp_path):result=scheduler.tick()
        assert result['continuous_through']=='2026-09-28'
        assert [r['task_id'] for r in repo.list_runs('2026-09-28','2026-09-30')]==[r['task_id'] for r in old]

def test_tick_bounded_to_31_new_days_without_skipping_remaining(tmp_path):
    with database() as factory:
        repo=store(factory);scheduler=make(repo,tmp_path,'2026-03-31T12:00:00Z',bootstrap_start='2026-01-01')
        def many(start,end,now):
            data=provider(start,end,now)
            for row in data['rows']:row['is_open']=1
            return data
        scheduler.calendar_provider=many
        with lock(repo,tmp_path):
            scheduler.tick();assert len(repo.list_runs('2026-01-01','2026-03-31'))==31
            scheduler.tick();assert len(repo.list_runs('2026-01-01','2026-03-31'))==62

def test_permanent_calendar_error_blocks_refresh_until_explicit_locked_reset(tmp_path):
    with database() as factory:
        repo=store(factory);scheduler=make(repo,tmp_path,bootstrap_start='2026-09-28');calls=[]
        def fail(*args):calls.append(1);raise PreparationFailure('PERMISSION')
        scheduler.calendar_provider=fail
        with lock(repo,tmp_path):
            scheduler.tick();scheduler.tick()
            assert calls==[1] and scheduler.status()['blocked_error']=='PERMISSION'
            scheduler.reset_error();scheduler.tick();assert calls==[1,1]
        with pytest.raises(RuntimeError,match='WORKER_LOCK_REQUIRED'):scheduler.reset_error()

def test_quota_wait_is_future_persisted_and_does_not_refresh_again(tmp_path):
    with database() as factory:
        repo=store(factory);scheduler=make(repo,tmp_path,bootstrap_start='2026-09-28');calls=[]
        def fail(*args):calls.append(1);raise PreparationFailure('HOURLY_QUOTA',retry_not_before=10)
        scheduler.calendar_provider=fail
        with lock(repo,tmp_path):scheduler.tick();scheduler.tick()
        assert calls==[1] and datetime.fromisoformat(scheduler.status()['retry_not_before']).timestamp()==scheduler.clock()+60

def test_corrupt_control_is_blocked_and_not_overwritten(tmp_path):
    with database() as factory:
        repo=store(factory);path=tmp_path/'nightly_scheduler_state.json';path.write_bytes(b'broken')
        scheduler=make(repo,tmp_path,bootstrap_start='2026-09-28')
        with lock(repo,tmp_path):assert scheduler.tick()['blocked_error']=='SCHEMA'
        assert path.read_bytes()==b'broken' and repo.earliest_date() is None

def test_tick_requires_owned_root_lock(tmp_path):
    with database() as factory:
        scheduler=make(store(factory),tmp_path)
        with pytest.raises(RuntimeError,match='WORKER_LOCK_REQUIRED'):scheduler.tick()

def test_changing_schedule_does_not_mutate_existing_run(tmp_path):
    with database() as factory:
        repo=store(factory)
        with lock(repo,tmp_path):make(repo,tmp_path,bootstrap_start='2026-09-28').tick()
        original=repo.get(repo.list_runs('2026-09-28','2026-09-28')[0]['task_id'])
        with lock(repo,tmp_path):make(repo,tmp_path,'2026-09-29T13:00:00Z',run_at='21:00').tick()
        assert repo.get(original['task_id'])['request']==original['request']
        latest=repo.get(repo.list_runs('2026-09-29','2026-09-29')[0]['task_id'])
        assert latest['request']['scheduled_for'].endswith('T21:00:00+08:00')
