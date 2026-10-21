"""Fixed worker entrypoint. No model dependency is loaded before control handshake."""
import json
import os
import signal
import sys
import threading
import time


def _abort():
    if os.name!='nt':os.killpg(os.getpgrp(),signal.SIGKILL)
    os._exit(70)


def _guard():
    last=[time.monotonic()]
    def read():
        try:
            while True:
                if sys.stdin.buffer.readline(16)!=b'PING\n':_abort()
                last[0]=time.monotonic()
        except Exception:_abort()
    threading.Thread(target=read,daemon=True).start()
    def watch():
        while True:
            if time.monotonic()-last[0]>15:_abort()
            time.sleep(1)
    threading.Thread(target=watch,daemon=True).start()


def main():
    raw=sys.stdin.buffer.readline(65537)
    if len(raw)>65536 or not raw.endswith(b'\n'):return 70
    payload=json.loads(raw)
    if not isinstance(payload,dict) or set(payload) not in ({'task','root'},{'task_id','worker_id','generation','expected_task_sha','root'}):return 70
    referenced='task_id' in payload
    if referenced:
        from .contracts import identity,bound_integer
        identity(payload['task_id']);identity(payload['worker_id']);bound_integer(payload['generation'],1,2**63-1)
        if not isinstance(payload['root'],str) or not isinstance(payload['expected_task_sha'],str):return 70
    _guard()
    from .contracts import encoded,TaskFailure
    protocol=sys.stdout
    def emit(event,**data):
        protocol.write(encoded(dict(event=event,**data))+'\n');protocol.flush()
    try:
        import contextlib
        with contextlib.redirect_stdout(sys.stderr):
            from database.db_connector import DatabaseConnector
            from .source import SourceProvider,SourceChanged
            from .runner import run_xgboost
            with DatabaseConnector(diagnostic=True) as db:
                if referenced:
                    from .repository import TaskRepository
                    from .service import TaskService
                    from .market_source import MarketSource
                    from .lightgbm_runner import run_lightgbm
                    source=MarketSource(db.engine)
                    task=TaskService(TaskRepository(db.engine),source).execution_task(payload['task_id'],payload['worker_id'],
                        payload['generation'],payload['expected_task_sha'])
                    if task['request']['kind']=='model_workflow':
                        from workflows.source import WorkflowSource
                        from workflows.runner import run_operation
                        run_operation(task,payload['root'],WorkflowSource(db.engine),emit)
                    else:run_lightgbm(task,payload['root'],source,emit)
                else:run_xgboost(payload['task'],payload['root'],SourceProvider(db.engine),lambda stage:emit('stage',stage=stage))
        emit('complete');return 0
    except Exception as error:
        code='SOURCE_CHANGED' if type(error).__name__=='SourceChanged' else 'INPUT_INVALID' if isinstance(error,ValueError) else 'TRAINING_FAILED'
        if isinstance(error,TaskFailure):code=error.code
        emit('failure',code=code);return 1


if __name__=='__main__':
    # The control reader deliberately waits on stdin in a daemon thread.
    # Avoid interpreter finalization attempting to close that locked buffer.
    # All model files/DB contexts and protocol output are flushed before return.
    try:code=main()
    except Exception:code=70
    os._exit(code)
