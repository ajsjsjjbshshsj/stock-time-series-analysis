"""Bounded DB lease renewal independent of slow supervisor work.

This controller grants no process ownership and never releases an execution
slot. The supervisor still proves its owned tree stopped before finalization.
"""
import logging
import threading
import time
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

    def start(self):
        def run():
            while not self.stopped.wait(self.interval):
                if self.clock()>=self.deadline:
                    self._fail('TIME_LIMIT');return
                begin=self.clock()
                try:
                    current=self.service.heartbeat(*self.fence)
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
                    self._fail('DEPENDENCY_UNAVAILABLE');return
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
