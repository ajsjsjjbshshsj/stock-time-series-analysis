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
    if not isinstance(payload,dict) or set(payload)!={'task','root'}:return 70
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
                run_xgboost(payload['task'],payload['root'],SourceProvider(db.engine),lambda stage:emit('stage',stage=stage))
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
