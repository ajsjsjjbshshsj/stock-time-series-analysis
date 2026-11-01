"""Single-slot worker supervisor; HTTP requests never call this module."""
import os
import logging
from pathlib import Path
import sys
import threading
import time
from uuid import uuid4
from .contracts import TaskFailure,LeaseLost
from .processes import OwnedProcess


class _LeasePulse:
    """Keep the pipe responsive, but never outlive a confirmed DB lease.

    The child watchdog takes at most 16s to notice its 15s silence limit.
    Reserve that entire interval before the lease deadline. Slow DB calls
    therefore do not starve healthy children or grant unbounded permission.
    """
    def __init__(self,process,*,clock=time.monotonic,wall_clock=time.time):
        self.process=process;self.clock=clock;self.wall_clock=wall_clock
        self.deadline=0.;self.closed=threading.Event();self.failed=threading.Event()
        self.lock=threading.Lock();self.thread=None

    def renew(self,lease_until):
        if type(lease_until) is not int:raise LeaseLost('Missing confirmed execution lease')
        remaining=min(30.,lease_until-self.wall_clock())
        with self.lock:self.deadline=self.clock()+max(0.,remaining-16.)

    def tick(self):
        with self.lock:
            if self.closed.is_set() or self.clock()>=self.deadline:return False
        # Pipe backpressure must not block the main thread's lease updates
        # or its eventual owned-tree termination.
        self.process.keepalive();return True

    def start(self):
        def run():
            try:
                while not self.closed.is_set():
                    self.tick()
                    self.closed.wait(1)
            except Exception:self.failed.set()
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()

    def close(self):
        self.closed.set()
        if self.thread is not None:self.thread.join(timeout=2)


class Worker:
    def __init__(self,service,root,*,process_factory=None,before_poll=None):
        self.service=service;self.root=Path(root).absolute();self.worker_id=uuid4().hex
        self.process_factory=process_factory or self._process
        self.before_poll=before_poll

    def _process(self):
        env=dict(os.environ,OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        return OwnedProcess([sys.executable,'-m','tasks.child'],cwd=Path(__file__).resolve().parents[1],env=env)

    def run_once(self):
        if self.before_poll is not None:self.before_poll()
        task=self.service.claim(self.worker_id)
        if task is None:return False
        process=None;pulse=None;outcome='FAILED';code='PROCESS_FAILED';candidate=None;interrupted=False
        fence=(task['id'],self.worker_id,task['generation'])
        market=task['request']['kind'] in ('lightgbm_all_market','model_workflow','feature_build','feature_activate')
        try:
            process=self.process_factory()
            if market:
                from .artifacts import task_digest
                from .market_profile import bounded_profile
                from .resources import check_tree,check_time
                profile=bounded_profile()
                process.resource_directory=self.root
                payload=dict(task_id=task['id'],worker_id=self.worker_id,generation=task['generation'],
                             expected_task_sha=task_digest(task),root=str(self.root))
            else:payload=dict(task={k:task[k] for k in ('id','request','binding')},root=str(self.root))
            current=self.service.heartbeat(*fence)
            if current['state']=='CANCEL_REQUESTED':
                outcome='CANCELLED';return True
            pulse=_LeasePulse(process);pulse.renew(current['lease_until'])
            process.start(payload);pulse.start()
            last_heartbeat=time.monotonic();last_ping=0.;started=time.monotonic();complete=False
            def drain(final=False):
                nonlocal complete
                for event in process.messages(final=final):
                    if event['event']=='stage':self.service.stage(*fence,event['stage'])
                    elif event['event']=='progress':self.service.progress(*fence,event['phase'],event['completed'],event['total'])
                    elif event['event']=='failure':raise TaskFailure(event['code'])
                    elif event['event']=='complete':
                        if complete:raise TaskFailure('PROTOCOL_INVALID')
                        complete=True
            while True:
                now=time.monotonic()
                if market:check_time(now-started,profile)
                elif now-started>1800:raise TaskFailure('PROCESS_FAILED')
                if now-last_heartbeat>=5:
                    current=self.service.heartbeat(*fence);last_heartbeat=now
                    if current['state']=='CANCEL_REQUESTED':outcome='CANCELLED';break
                    pulse.renew(current['lease_until'])
                if pulse.failed.is_set():raise TaskFailure('PROCESS_FAILED')
                if now-last_ping>=1:
                    last_ping=now
                    if market:check_tree(process,profile)
                drain()
                exit_code=process.poll()
                if exit_code is not None:
                    drain(final=True)
                    if exit_code!=0 or not complete:
                        logging.getLogger(__name__).warning('Owned task process exited: exit_code=%s complete=%s',exit_code,complete)
                        raise TaskFailure('PROCESS_FAILED')
                    if not process.stop():raise TaskFailure('PROCESS_FAILED')
                    from .artifacts import verify_candidate
                    try:
                        options=dict(engine=self.service.repo.engine) if task['request']['kind'] in ('feature_build','feature_activate') else {}
                        candidate=verify_candidate(task,self.root,**options)
                    except Exception:raise TaskFailure('CANDIDATE_INVALID') from None
                    self.service.heartbeat(*fence)
                    outcome='SUCCEEDED';break
                time.sleep(.05)
        except TaskFailure as error:code=error.code
        except (KeyboardInterrupt,SystemExit):code='INTERRUPTED';outcome='INTERRUPTED';interrupted=True
        except Exception:code='PROCESS_FAILED'
        finally:
            if pulse is not None:pulse.close()
            stopped=process.stop() if process is not None else True
            if stopped:
                try:self.service.finish(*fence,outcome,process_stopped=True,candidate=candidate,failure_code=code)
                except Exception:pass  # No DB confirmation: old slot remains unavailable.
            try:self.service.expire()
            except Exception:pass
        if interrupted:raise KeyboardInterrupt()
        return True

    def run(self):
        self.service.repo.check_ready()
        while True:
            try:
                if not self.run_once():time.sleep(1)
            except KeyboardInterrupt:return
            except Exception:time.sleep(1)
