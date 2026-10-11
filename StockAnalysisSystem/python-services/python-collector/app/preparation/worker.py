"""A single durable worker with process-proven recovery and independent heartbeat."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import threading
import time
import uuid

from .process_owner import current_owner,owner_alive
from app.market_data.forward_snapshot import read


class PreparationWorker:
    def __init__(self,repository,service,*,clock=time.time,sleep=time.sleep,owner_probe=owner_alive,heartbeat_interval=30):
        self.repository=repository; self.service=service; self.clock=clock; self.sleep=sleep
        self.owner_probe=owner_probe; self.heartbeat_interval=heartbeat_interval
        if heartbeat_interval<=0 or heartbeat_interval>=120: raise ValueError('HEARTBEAT_INTERVAL_INVALID')
        self.owner=current_owner(uuid.uuid4().hex)
        self.stopped=False

    def _now(self,offset=0): return datetime.fromtimestamp(self.clock()+offset,timezone.utc).isoformat()

    @staticmethod
    def _create_lock(path,owner):
        try: fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError: raise RuntimeError('WORKER_BUSY') from None
        try:
            os.write(fd,json.dumps(owner,ensure_ascii=False).encode('utf-8')); os.fsync(fd)
        finally: os.close(fd)

    @contextmanager
    def _lock(self):
        root=Path(self.service.data_root).resolve(); root.mkdir(parents=True,exist_ok=True)
        startup=root/'worker.start.lock'; lock=root/'worker.lock'
        self._create_lock(startup,self.owner)
        acquired=False
        try:
            if lock.exists():
                try: old=read(lock)
                except (OSError,ValueError): raise RuntimeError('RECOVERY_CONFIRMATION_REQUIRED') from None
                if self.owner_probe(old) is not False: raise RuntimeError('RECOVERY_CONFIRMATION_REQUIRED')
                # All worker starts take startup first, closing the stale-lock
                # unlink/recreate race. Request locks are deliberately untouched.
                lock.unlink()
            self._create_lock(lock,self.owner); acquired=True
        finally:
            if startup.exists() and read(startup).get('token')==self.owner['token']: startup.unlink()
        try: yield
        finally:
            if acquired and lock.exists() and read(lock).get('token')==self.owner['token']: lock.unlink()

    def _recover(self):
        for task in self.repository.expired(now=self._now()):
            if self.owner_probe(task['owner']) is not False:
                raise RuntimeError('RECOVERY_CONFIRMATION_REQUIRED')
            self.repository.recover(task['task_id'],task['owner_token'],now=self._now())

    def _run_once(self):
        self._recover()
        task=self.repository.claim(self.owner,now=self._now(),lease_until=self._now(120))
        if task is None: return None
        stop=threading.Event()
        def heartbeat():
            while not stop.wait(self.heartbeat_interval):
                try:
                    if not self.repository.heartbeat(task['task_id'],self.owner['token'],now=self._now(),lease_until=self._now(120)):
                        return
                except Exception:
                    return  # Writes remain fenced; never guess that a failed renew succeeded.
        thread=threading.Thread(target=heartbeat,name='preparation-heartbeat',daemon=True)
        thread.start()
        try: return self.service.execute(task,owner_token=self.owner['token'])
        finally:
            stop.set(); thread.join(timeout=min(10,self.heartbeat_interval))

    def run_once(self):
        with self._lock(): return self._run_once()

    def run_forever(self):
        with self._lock():
            while not self.stopped:
                result=self._run_once()
                if result is not None: continue
                deadline=self.repository.next_retry()
                delay=max(1,min(60,datetime.fromisoformat(deadline).timestamp()-self.clock())) if deadline else 5
                self.sleep(delay)

    def stop(self): self.stopped=True
