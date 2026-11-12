"""Bounded DB lease renewal independent of slow supervisor work.

This controller grants no process ownership and never releases an execution
slot. The supervisor still proves its owned tree stopped before finalization.
"""
import logging
import threading
import time
from sqlalchemy.exc import OperationalError
from .contracts import TaskFailure


def connection_config(config):
    """Keep control I/O bounded without shortening source-query budgets."""
    return dict(config,connect_timeout=5,read_timeout=5,write_timeout=5,pool_timeout=5)


class TaskControl:
    def __init__(self,service,fence,pulse,*,max_seconds,interval=5,clock=time.monotonic):
        self.service=service;self.fence=fence;self.pulse=pulse
        self.clock=clock;self.deadline=clock()+max_seconds;self.interval=interval
        self.stopped=threading.Event();self.cancelled=threading.Event()
        self.failure=None;self.thread=None

    def _fail(self,code):
        self.failure=code;self.stopped.set();self.pulse.close()

    def _heartbeat(self):
        try:return self.service.heartbeat(*self.fence)
        except OperationalError as error:
            args=getattr(error.orig,'args',())
            number=args[0] if args and type(args[0]) is int else None
            # One fresh transaction only. Keep sending solely under the last
            # CONFIRMED pulse deadline; do not extend a lease on an exception.
            # Authentication, ownership, schema and unknown errors never retry.
            if number not in (1205,1213,2003,2006,2013) or self.stopped.is_set() or self.clock()>=self.deadline:raise
            logging.getLogger(__name__).warning('Task control retry: operation=heartbeat db_errno=%d attempt=1',number)
            return self.service.heartbeat(*self.fence)

    def start(self):
        def run():
            while not self.stopped.wait(self.interval):
                if self.clock()>=self.deadline:
                    self._fail('TIME_LIMIT');return
                begin=self.clock()
                try:
                    current=self._heartbeat()
                    if self.stopped.is_set():return
                    if self.clock()>=self.deadline:
                        self._fail('TIME_LIMIT');return
                    if current['state']=='CANCEL_REQUESTED':
                        self.cancelled.set();self.stopped.set();self.pulse.close();return
                    if current['state']!='RUNNING':raise ValueError('Invalid control state')
                    self.pulse.renew(current['lease_until'])
                except Exception as error:
                    logging.getLogger(__name__).warning(
                        'Task control failed: operation=heartbeat exception_type=%s elapsed_ms=%d',
                        type(error).__name__,int(1000*(self.clock()-begin)))
                    self._fail('TIME_LIMIT' if self.clock()>=self.deadline else 'DEPENDENCY_UNAVAILABLE');return
        self.thread=threading.Thread(target=run,daemon=True,name='task-lease-control')
        self.thread.start()

    def check(self):
        if self.failure:raise TaskFailure(self.failure)
        if self.cancelled.is_set():return 'CANCEL_REQUESTED'
        if self.clock()>=self.deadline:raise TaskFailure('TIME_LIMIT')
        return 'RUNNING'

    def close(self,timeout=2):
        self.stopped.set()
        if self.thread is None:return True
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()
