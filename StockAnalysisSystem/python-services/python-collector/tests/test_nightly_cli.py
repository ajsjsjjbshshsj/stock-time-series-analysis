from pathlib import Path
import sys,json
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parent))
from datetime import datetime
import pytest
from test_prepare_stock_pool_data import cli
from test_nightly_service import make
from nightly_store_support import database

def test_worker_default_stays_legacy_without_nightly(capsys,monkeypatch):
    module=cli();calls=[]
    monkeypatch.setenv('NIGHTLY_UPDATE_AT','invalid-but-disabled')
    worker=SimpleNamespace(run_once=lambda:None)
    def old_worker():calls.append('legacy');return worker
    assert module.main(['worker','--once'],dependencies={'worker':old_worker})==0
    assert calls==['legacy'] and json.loads(capsys.readouterr().out)['status']=='IDLE'

def test_runtime_new_nightly_queue_uses_independent_period_version():
    module=cli()
    with database() as factory:
        runtime=module.Runtime.__new__(module.Runtime)
        runtime.db=SimpleNamespace(Session=factory);runtime._market_tasks=None
        assert runtime.market_tasks().requirements_version=='market-v2'

def test_nightly_worker_explicit_defaults_and_environment(capsys,monkeypatch):
    module=cli();calls=[];monkeypatch.delenv('NIGHTLY_UPDATE_AT',raising=False)
    def worker(**kwargs):calls.append(kwargs);return SimpleNamespace(run_once=lambda:None)
    assert module.main(['worker','--nightly','--once'],dependencies={'worker':worker})==0
    assert calls==[dict(nightly=True,run_at='20:00',bootstrap_start=None)]
    capsys.readouterr();monkeypatch.setenv('NIGHTLY_UPDATE_AT','21:00')
    assert module.main(['worker','--nightly','--once'],dependencies={'worker':worker})==0
    assert calls[-1]['run_at']=='21:00'

@pytest.mark.parametrize('args',[
    ['worker','--nightly-at','20:00'],['worker','--bootstrap-start','2026-09-28'],
    ['worker','--nightly','--nightly-at','16:00'],['worker','--nightly','--bootstrap-start','2022-12-30'],
    ['nightly-submit','--date','bad'],['nightly-submit','--date','2022-12-30'],
    ['nightly-retry'],['nightly-retry','--scheduler','--task-id','a'*32]])
def test_invalid_nightly_arguments_fail_before_runtime_initialization(args,monkeypatch):
    module=cli();calls=[]
    monkeypatch.setattr(module,'load_environment',lambda:None,raising=False)
    monkeypatch.setattr(module,'Runtime',lambda:calls.append('unexpected'))
    with pytest.raises(SystemExit) as caught:module.main(args)
    assert caught.value.code==2 and calls==[]

def test_nightly_submit_and_status_use_actual_emitted_task_id_without_sdk(tmp_path,capsys):
    module=cli()
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path)
        deps=dict(market_service=lambda:service,market_tasks=lambda:tasks)
        assert module.main(['nightly-submit','--date','2026-09-28'],dependencies=deps)==0
        result=json.loads(capsys.readouterr().out);task_id=result['task_id']
        assert result['status']=='QUEUED' and calls==[] and market.writes==0
        assert module.main(['nightly-status','--task-id',task_id],dependencies=deps)==0
        assert json.loads(capsys.readouterr().out)['task_id']==task_id and calls==[]

def test_nightly_submit_before_20_is_rejected_without_market_calls(tmp_path,capsys):
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path)
        service.clock=lambda:datetime.fromisoformat('2026-09-28T11:00:00Z').timestamp()
        assert cli().main(['nightly-submit','--date','2026-09-28'],dependencies=dict(market_service=lambda:service))!=0
        assert tasks.earliest_date() is None and calls==[] and market.writes==0
        assert 'token' not in capsys.readouterr().out.lower()

def test_nightly_public_universe_is_summary_not_full_members(capsys):
    task=dict(task_id='a'*32,status='SUCCEEDED',owner_token='private',owner={'token':'private'},
        universe=dict(scope='SH_SZ_A',stock_count=1,member_sha256='b'*64,acquired_at='2026-09-28T20:00:00+08:00',codes=['000001.SZ'],members=[{'name':'do-not-print'}]))
    assert cli().main(['nightly-status','--task-id','a'*32],dependencies=dict(market_tasks=lambda:SimpleNamespace(get=lambda task_id:task)))==0
    output=capsys.readouterr().out;value=json.loads(output)
    assert 'private' not in output and 'members' not in output and 'codes' not in output
    assert value['universe']['stock_count']==1

def test_scheduler_reset_takes_maintenance_lock_and_leaves_quota_alone(capsys):
    from contextlib import contextmanager
    events=[]
    @contextmanager
    def lock():
        events.append('lock');yield;events.append('unlock')
    def reset():assert events==['lock'];events.append('reset')
    scheduler=SimpleNamespace(reset_error=reset,status=lambda:dict(blocked_error=None))
    deps=dict(worker=lambda:SimpleNamespace(maintenance_lock=lock),scheduler=lambda **kwargs:scheduler)
    assert cli().main(['nightly-retry','--scheduler'],dependencies=deps)==0
    assert events==['lock','reset','unlock'] and json.loads(capsys.readouterr().out)['blocked_error'] is None

def test_scheduler_reset_busy_is_safe_nonzero(capsys):
    from contextlib import contextmanager
    @contextmanager
    def busy():raise RuntimeError('WORKER_BUSY');yield
    deps=dict(worker=lambda:SimpleNamespace(maintenance_lock=busy),scheduler=lambda **kwargs:pytest.fail('busy must precede reset'))
    assert cli().main(['nightly-retry','--scheduler'],dependencies=deps)==2
    assert json.loads(capsys.readouterr().out)['error_kind']=='WORKER_BUSY'

def test_runtime_reuses_and_closes_only_owned_http_session(monkeypatch):
    module=cli();runtime=module.Runtime.__new__(module.Runtime);events=[]
    runtime._http_session=None;runtime._tushare_client=None
    runtime.db=SimpleNamespace(close=lambda:events.append('db-close'))
    session=SimpleNamespace(close=lambda:events.append('session-close'))
    import requests
    monkeypatch.setattr(requests,'Session',lambda:events.append('session-create') or session)
    monkeypatch.setitem(sys.modules,'app.config',SimpleNamespace(TUSHARE_TOKEN='unit-test-only'))
    from app.market_data.tushare_client import TushareClient
    client=SimpleNamespace(close=lambda:pytest.fail('must not invoke dynamic provider close endpoint'))
    def create(token,**kwargs):assert kwargs==dict(timeout=20,session=session);return client
    monkeypatch.setattr(TushareClient,'from_token',create)
    assert runtime.client() is client and runtime.client() is client
    runtime.close();assert events==['session-create','db-close','session-close']

def test_nightly_retry_real_partial_task_preserves_identity_and_increases_attempt(tmp_path,capsys):
    from test_nightly_service import execute
    with database() as factory:
        service,tasks,market,calls,_=make(factory,tmp_path,conflict=True)
        previous=execute(service,tasks);assert previous['status']=='PARTIAL'
        calls.clear()
        assert cli().main(['nightly-retry','--task-id',previous['task_id']],dependencies=dict(market_tasks=lambda:tasks))==0
        value=json.loads(capsys.readouterr().out)
        assert value['task_id']==previous['task_id'] and value['status']=='QUEUED' and value['attempt_no']==2
        assert calls==[] and market.writes==0

def test_nightly_status_control_and_run_summary_only_needs_read_dependencies(capsys):
    queries=[]
    control=dict(start_date='2026-09-28',blocked_error=None,run_at='20:00')
    def listing(start,end):queries.append((start,end));return [dict(task_id='a'*32,status='PARTIAL',trade_date='2026-09-28')]
    deps=dict(scheduler=lambda:SimpleNamespace(status=lambda:control),market_tasks=lambda:SimpleNamespace(list_runs=listing))
    assert cli().main(['nightly-status'],dependencies=deps)==0
    value=json.loads(capsys.readouterr().out)
    assert value['scheduler']==control and value['runs'][0]['status']=='PARTIAL' and queries[0][0]=='2026-09-28'

def test_nightly_status_hides_private_upstream_exception(capsys):
    def fail(task_id):raise RuntimeError('request token private-do-not-show')
    assert cli().main(['nightly-status','--task-id','a'*32],dependencies=dict(market_tasks=lambda:SimpleNamespace(get=fail)))==2
    captured=capsys.readouterr()
    assert 'private-do-not-show' not in captured.out+captured.err
    assert json.loads(captured.out)['error_kind']=='PREPARATION_FAILED'

def test_market_repository_is_lazy_and_cached_without_sdk_or_sql():
    module=cli();runtime=module.Runtime.__new__(module.Runtime)
    runtime._market_tasks=None;runtime.db=SimpleNamespace(Session=lambda:pytest.fail('constructor must not query SQL'))
    one=runtime.market_tasks();assert runtime.market_tasks() is one

def fake_runtime(module,monkeypatch,environment,events):
    monkeypatch.setattr(module,'load_environment',environment,raising=False)
    def construct():
        events.append('runtime');environment() # Mimic Runtime's existing dotenv initialization.
        names=('pools','tasks','service','evidence','calendar','pool_sources','market_tasks','market_service','scheduler')
        obj=SimpleNamespace(**{name:None for name in names})
        obj.worker=lambda **kwargs:events.append(kwargs) or SimpleNamespace(run_once=lambda:None)
        obj.close=lambda:events.append('closed')
        return obj
    monkeypatch.setattr(module,'Runtime',construct)

def test_project_dotenv_nightly_settings_are_loaded_before_resolving_worker_options(monkeypatch,capsys):
    module=cli();events=[]
    monkeypatch.delenv('NIGHTLY_UPDATE_AT',raising=False);monkeypatch.delenv('NIGHTLY_BOOTSTRAP_START',raising=False)
    def environment():
        monkeypatch.setenv('NIGHTLY_UPDATE_AT','21:00');monkeypatch.setenv('NIGHTLY_BOOTSTRAP_START','2023-01-01')
    fake_runtime(module,monkeypatch,environment,events)
    assert module.main(['worker','--nightly','--once'])==0
    assert events[1]==dict(nightly=True,run_at='21:00',bootstrap_start='2023-01-01')
    assert json.loads(capsys.readouterr().out)['status']=='IDLE'

def test_invalid_project_dotenv_time_fails_before_runtime_db_or_sdk(monkeypatch):
    module=cli();events=[]
    monkeypatch.delenv('NIGHTLY_UPDATE_AT',raising=False);monkeypatch.delenv('NIGHTLY_BOOTSTRAP_START',raising=False)
    fake_runtime(module,monkeypatch,lambda:monkeypatch.setenv('NIGHTLY_UPDATE_AT','16:00'),events)
    with pytest.raises(SystemExit) as caught:module.main(['worker','--nightly','--once'])
    assert caught.value.code==2 and events==[]

def test_explicit_options_override_project_dotenv_nightly_settings(monkeypatch,capsys):
    module=cli();events=[]
    def environment():
        monkeypatch.setenv('NIGHTLY_UPDATE_AT','21:00');monkeypatch.setenv('NIGHTLY_BOOTSTRAP_START','2023-01-01')
    fake_runtime(module,monkeypatch,environment,events)
    assert module.main(['worker','--nightly','--once','--nightly-at','20:00','--bootstrap-start','2026-09-28'])==0
    assert events[1]==dict(nightly=True,run_at='20:00',bootstrap_start='2026-09-28')
