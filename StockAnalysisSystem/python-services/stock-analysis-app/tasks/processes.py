"""Owned OS process trees and a bounded protocol, never a user command endpoint."""
import json
import os
import queue
import signal
import subprocess
import threading
import time
from .contracts import encoded,TaskFailure


def parse_message(raw):
    if len(raw)>4096 or not raw.endswith(b'\n'):raise ValueError('Protocol bound')
    body=json.loads(raw,parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Protocol number')))
    if not isinstance(body,dict):raise ValueError('Protocol schema')
    if set(body)=={'event','stage'} and body['event']=='stage' and body['stage'] in ('PREFLIGHT','INPUT','TRAIN','VERIFY','SAVE'):return body
    if set(body)=={'event'} and body['event']=='complete':return body
    if set(body)=={'event','code'} and body['event']=='failure':
        return dict(event='failure',code=TaskFailure(body['code']).code)
    raise ValueError('Protocol schema')


class _WindowsJob:
    def __init__(self):
        import ctypes as ct
        from ctypes import wintypes as wt
        self.ct=ct;self.kernel=ct.WinDLL('kernel32',use_last_error=True)
        class Basic(ct.Structure):
            _fields_=[('user',ct.c_int64),('job',ct.c_int64),('flags',wt.DWORD),
                ('min_ws',ct.c_size_t),('max_ws',ct.c_size_t),('active',wt.DWORD),
                ('affinity',ct.c_size_t),('priority',wt.DWORD),('scheduling',wt.DWORD)]
        class IO(ct.Structure):_fields_=[(name,ct.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
        class Extended(ct.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_memory',ct.c_size_t),('job_memory',ct.c_size_t),
                ('peak_process',ct.c_size_t),('peak_job',ct.c_size_t)]
        class Accounting(ct.Structure):
            _fields_=[(name,ct.c_int64) for name in ('total_user','total_kernel','period_user','period_kernel')]+[
                (name,wt.DWORD) for name in ('faults','total','active','terminated')]
        self.Accounting=Accounting
        signatures={
            'CreateJobObjectW':([ct.c_void_p,wt.LPCWSTR],wt.HANDLE),
            'SetInformationJobObject':([wt.HANDLE,ct.c_int,ct.c_void_p,wt.DWORD],wt.BOOL),
            'AssignProcessToJobObject':([wt.HANDLE,wt.HANDLE],wt.BOOL),
            'TerminateJobObject':([wt.HANDLE,wt.UINT],wt.BOOL),
            'QueryInformationJobObject':([wt.HANDLE,ct.c_int,ct.c_void_p,wt.DWORD,ct.c_void_p],wt.BOOL),
            'CloseHandle':([wt.HANDLE],wt.BOOL)}
        for name,(args,result) in signatures.items():
            function=getattr(self.kernel,name);function.argtypes=args;function.restype=result
        self.handle=self.kernel.CreateJobObjectW(None,None)
        if not self.handle:raise OSError('Process isolation unavailable')
        info=Extended();info.basic.flags=0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle,9,ct.byref(info),ct.sizeof(info)):
            self.close();raise OSError('Process isolation unavailable')

    def assign(self,process):
        if not self.kernel.AssignProcessToJobObject(self.handle,int(process._handle)):
            raise OSError('Process isolation unavailable')

    def terminate(self):
        if self.handle and not self.kernel.TerminateJobObject(self.handle,70):raise OSError('Owned job stop failed')

    def empty(self):
        info=self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle,1,self.ct.byref(info),self.ct.sizeof(info),None):
            raise OSError('Owned job status unavailable')
        return info.active==0

    def close(self):
        if self.handle:self.kernel.CloseHandle(self.handle);self.handle=None


class OwnedProcess:
    """Internal primitive; callers keep the Popen handle, not a reusable bare PID."""
    def __init__(self,command,*,cwd,env=None):
        self.command=list(command);self.cwd=cwd;self.env=env
        self.process=None;self.job=None;self._assigned=False;self._stopped=False
        self.protocol_error=threading.Event();self.reader_done=threading.Event();self._messages=queue.Queue(maxsize=64)

    def start(self,payload):
        line=encoded(payload).encode()+b'\n'
        if len(line)>65536:raise ValueError('Task control bound')
        try:
            if os.name=='nt':self.job=_WindowsJob()
            self.process=subprocess.Popen(self.command,cwd=self.cwd,env=self.env,stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0,start_new_session=os.name!='nt')
            if self.job:self.job.assign(self.process);self._assigned=True
            # Child waits on this first line: it cannot train/spawn before isolation.
            self.process.stdin.write(line);self.process.stdin.flush()
            threading.Thread(target=self._read,daemon=True).start()
        except BaseException:
            if self.process and not self._assigned and self.process.poll() is None:
                self.process.terminate();self.process.wait(timeout=5)
            self.stop();raise

    def _read(self):
        try:
            count=0
            while True:
                raw=self.process.stdout.readline(4097)
                if not raw:return
                count+=1
                if count>2000:raise ValueError('Too many events')
                message=parse_message(raw)
                self._messages.put_nowait(message)
        except Exception:self.protocol_error.set()
        finally:self.reader_done.set()

    def messages(self,final=False):
        if final and not self.reader_done.wait(timeout=5):raise TaskFailure('PROTOCOL_INVALID')
        if self.protocol_error.is_set():raise TaskFailure('PROTOCOL_INVALID')
        messages=[]
        while True:
            try:messages.append(self._messages.get_nowait())
            except queue.Empty:return messages

    def keepalive(self):
        if self.process and self.process.poll() is None:
            try:self.process.stdin.write(b'PING\n');self.process.stdin.flush()
            except (BrokenPipeError,OSError):
                # Closing stdin can precede the visible OS exit by a few ticks.
                # Completion still requires exit status + drained protocol + files.
                pass

    def poll(self):return self.process.poll()

    def stop(self):
        if self._stopped:return True
        if self.process is None:
            if self.job:self.job.close()
            self._stopped=True;return True
        try:
            if self.job and self._assigned:
                self.job.terminate()
            elif self.process.poll() is None:
                if os.name=='nt':self.process.terminate()
                else:os.killpg(self.process.pid,signal.SIGKILL)
            self.process.wait(timeout=5)
            if self.job and self._assigned:
                deadline=time.monotonic()+5
                while not self.job.empty():
                    if time.monotonic()>deadline:return False
                    time.sleep(.05)
            elif os.name!='nt':
                try:os.killpg(self.process.pid,0)
                except ProcessLookupError:pass
                else:return False  # Unknown descendants: retain DB quarantine.
            if self.job:self.job.close()
            for pipe in (self.process.stdin,self.process.stdout):
                if pipe:
                    try:pipe.close()
                    except OSError:pass
            self._stopped=True;return True
        except (OSError,subprocess.TimeoutExpired):return False
