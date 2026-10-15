from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

from datetime import datetime
import importlib
import time

import pytest
from preparation_store_support import database,load,pool,request


def cls():
    try: return importlib.import_module('app.preparation.worker').PreparationWorker
    except ModuleNotFoundError: pytest.fail('preparation worker not implemented')


def setup(factory):
    p=load('pool_repository','PoolRepository')(factory).register(pool(),activate=True)
    tasks=load('preparation_repository','PreparationRepository')(factory)
    task=tasks.submit(request(p['pool_version']),now='2026-10-11T09:00:00Z')
    return tasks,task


class Service:
    def __init__(self,tasks,root): self.tasks=tasks; self.data_root=root; self.calls=[]
    def execute(self,task,*,owner_token):
        self.calls.append(task['task_id'])
        assert self.tasks.update(task['task_id'],owner_token,dict(status='SUCCEEDED',result={'data_ready':True},updated_at='2026-10-11T09:03:00Z'))
        return self.tasks.get(task['task_id'])


NOW=lambda:datetime.fromisoformat('2026-10-11T09:03:00Z').timestamp()


def test_worker_claims_and_completes_single_task(tmp_path):
    typ=cls()
    with database() as factory:
        tasks,task=setup(factory); service=Service(tasks,tmp_path)
        result=typ(tasks,service,clock=NOW,owner_probe=lambda owner:False).run_once()
        assert result['task_id']==task['task_id']
        assert result['status']=='SUCCEEDED'
        assert service.calls==[task['task_id']]


def test_dead_expired_owner_can_resume_without_discarding_task_identity(tmp_path):
    typ=cls()
    with database() as factory:
        tasks,task=setup(factory)
        tasks.claim(dict(token='1'*32,pid=999999,started_at='2026-10-11T08:00:00Z',host='test'),
                    now='2026-10-11T09:00:00Z',lease_until='2026-10-11T09:02:00Z')
        result=typ(tasks,Service(tasks,tmp_path),clock=NOW,owner_probe=lambda owner:False).run_once()
        assert result['task_id']==task['task_id']
        assert result['status']=='SUCCEEDED'


@pytest.mark.parametrize('owner_alive',[True,None])
def test_live_or_uncertain_owner_is_not_stolen(tmp_path,owner_alive):
    typ=cls()
    with database() as factory:
        tasks,task=setup(factory)
        tasks.claim(dict(token='1'*32,pid=999999,started_at='2026-10-11T08:00:00Z',host='test'),
                    now='2026-10-11T09:00:00Z',lease_until='2026-10-11T09:02:00Z')
        service=Service(tasks,tmp_path)
        with pytest.raises(RuntimeError,match='RECOVERY_CONFIRMATION_REQUIRED'):
            typ(tasks,service,clock=NOW,owner_probe=lambda owner:owner_alive).run_once()
        assert service.calls==[]
        assert tasks.get(task['task_id'])['owner_token']=='1'*32


def test_heartbeat_runs_independently_during_slow_service(tmp_path):
    typ=cls(); beats=[]
    class Tasks:
        def expired(self,**kwargs): return []
        def claim(self,owner,**kwargs): return dict(task_id='a'*32,owner_token=owner['token'])
        def heartbeat(self,*args,**kwargs): beats.append(1); return True
    class Slow:
        data_root=tmp_path
        def execute(self,task,*,owner_token):
            time.sleep(.08)
            return dict(task,status='SUCCEEDED')
    result=typ(Tasks(),Slow(),clock=NOW,heartbeat_interval=.01).run_once()
    assert result['status']=='SUCCEEDED'
    assert len(beats)>=2


def test_naive_process_creation_time_is_unknown_not_proof_of_death(monkeypatch):
    import socket
    module=importlib.import_module('app.preparation.process_owner')
    monkeypatch.setattr(module,'_process',lambda pid:dict(pid=pid,started_at='2026-10-11T09:00:00Z'))
    owner=dict(pid=101,host=socket.gethostname(),started_at='2026-10-11T09:00:00')
    assert module.owner_alive(owner) is None


def test_idle_worker_waits_bounded_time_until_quota_deadline(tmp_path):
    typ=cls(); delays=[]
    class Tasks:
        def expired(self,**kwargs): return []
        def claim(self,*args,**kwargs): return None
        def next_retry(self): return '2026-10-11T10:03:00Z'
    class Idle: data_root=tmp_path
    worker=typ(Tasks(),Idle(),clock=NOW)
    def sleep(delay): delays.append(delay); worker.stop()
    worker.sleep=sleep
    worker.run_forever()
    assert delays==[60]


@pytest.mark.parametrize('returncode,stdout,expected',[
    (10,'',False),
    (1,'',None),
    (0,'{"pid": 7, "started_at": "2026-10-11T09:00:00.0000000Z"}',
       {'pid':7,'started_at':'2026-10-11T09:00:00.0000000Z'}),
    (0,'',False),
])
def test_windows_probe_maps_explicit_absent_exit_code(monkeypatch,returncode,stdout,expected):
    module=importlib.import_module('app.preparation.process_owner')
    class Result: pass
    result=Result(); result.returncode=returncode; result.stdout=stdout
    monkeypatch.setattr(module.subprocess,'run',lambda *args,**kwargs:result)
    monkeypatch.setattr(module.os,'name','nt')
    assert module._process(7)==expected


def test_confirmed_absent_owner_probe_allows_recovery(monkeypatch):
    import socket
    module=importlib.import_module('app.preparation.process_owner')
    monkeypatch.setattr(module,'_process',lambda pid:False)
    owner=dict(pid=101,host=socket.gethostname(),started_at='2026-10-11T09:00:00Z')
    assert module.owner_alive(owner) is False
