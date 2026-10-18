"""Single-slot worker supervisor; HTTP requests never call this module."""
import os
from pathlib import Path
import sys
import time
from uuid import uuid4
from .contracts import TaskFailure,LeaseLost
from .processes import OwnedProcess


class Worker:
    def __init__(self,service,root,*,process_factory=None):
        self.service=service;self.root=Path(root).absolute();self.worker_id=uuid4().hex
        self.process_factory=process_factory or self._process

    def _process(self):
        env=dict(os.environ,OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',OPENBLAS_NUM_THREADS='1')
        return OwnedProcess([sys.executable,'-m','tasks.child'],cwd=Path(__file__).resolve().parents[1],env=env)

    def run_once(self):
        task=self.service.claim(self.worker_id)
        if task is None:return False
        process=None;outcome='FAILED';code='PROCESS_FAILED';candidate=None;interrupted=False
        fence=(task['id'],self.worker_id,task['generation'])
        try:
            process=self.process_factory()
            process.start(dict(task={k:task[k] for k in ('id','request','binding')},root=str(self.root)))
            last_heartbeat=last_ping=0.;started=time.monotonic();complete=False
            def drain():
                nonlocal complete
                for event in process.messages():
                    if event['event']=='stage':self.service.stage(*fence,event['stage'])
                    elif event['event']=='failure':raise TaskFailure(event['code'])
                    elif event['event']=='complete':
                        if complete:raise TaskFailure('PROTOCOL_INVALID')
                        complete=True
            while True:
                now=time.monotonic()
                if now-started>1800:raise TaskFailure('PROCESS_FAILED')
                if now-last_heartbeat>=5:
                    current=self.service.heartbeat(*fence);last_heartbeat=now
                    if current['state']=='CANCEL_REQUESTED':outcome='CANCELLED';break
                if now-last_ping>=1:process.keepalive();last_ping=now
                drain()
                if process.poll() is not None:
                    # Reader can finish just after the OS exit becomes visible.
                    time.sleep(.05);drain()
                    if process.poll()!=0 or not complete:raise TaskFailure('PROCESS_FAILED')
                    if not process.stop():raise TaskFailure('PROCESS_FAILED')
                    from .artifacts import verify_candidate
                    try:candidate=verify_candidate(task,self.root)
                    except Exception:raise TaskFailure('CANDIDATE_INVALID') from None
                    self.service.heartbeat(*fence)
                    outcome='SUCCEEDED';break
                time.sleep(.05)
        except TaskFailure as error:code=error.code
        except (KeyboardInterrupt,SystemExit):code='INTERRUPTED';outcome='INTERRUPTED';interrupted=True
        except Exception:code='PROCESS_FAILED'
        finally:
            stopped=process.stop() if process is not None else True
            if stopped:
                try:self.service.finish(*fence,outcome,process_stopped=True,candidate=candidate,failure_code=code)
                except Exception:pass  # No DB confirmation: old slot remains unavailable.
            try:self.service.expire()
            except Exception:pass
        if interrupted:raise KeyboardInterrupt()
        return True

    def run(self):
        while True:
            try:
                if not self.run_once():time.sleep(1)
            except KeyboardInterrupt:return
            except Exception:time.sleep(1)
