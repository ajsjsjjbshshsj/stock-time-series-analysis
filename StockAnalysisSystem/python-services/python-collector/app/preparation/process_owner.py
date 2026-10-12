"""Read-only process identity probes. Unknown ownership is never treated as dead."""
from datetime import datetime,timezone
from pathlib import Path
import json
import os
import socket
import subprocess


def _process(pid):
    if type(pid) is not int or pid<=0: return None
    try:
        if os.name=='nt':
            # PS 5.1 exits 1 for a missing pid even with -ErrorAction SilentlyContinue, which
            # conflated "confirmed absent" with "probe failed" and blocked stale-owner recovery.
            # Explicit exit codes disambiguate: 10=absent (False), other nonzero=unknown (None).
            command=(f'$taskProcess=Get-Process -Id {pid} -ErrorAction SilentlyContinue; '
                "if($null -ne $taskProcess){[PSCustomObject]@{pid=$taskProcess.Id; started_at=$taskProcess.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress; exit 0} "
                'else {exit 10}')
            result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',command],
                capture_output=True,text=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode==10 and not result.stdout.strip(): return False
            if result.returncode: return None
            return json.loads(result.stdout) if result.stdout.strip() else False
        path=Path('/proc')/str(pid)
        if not path.exists(): return False
        stat=(path/'stat').read_text().rsplit(')',1)[1].split()
        boot=next(int(line.split()[1]) for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime '))
        seconds=boot+int(stat[19])/os.sysconf('SC_CLK_TCK')
        return dict(pid=pid,started_at=datetime.fromtimestamp(seconds,timezone.utc).isoformat())
    except (OSError,ValueError,KeyError,IndexError,StopIteration,subprocess.SubprocessError):
        return None


def current_owner(token):
    process=_process(os.getpid())
    if not process or not process.get('started_at'): raise RuntimeError('RECOVERY_CONFIRMATION_REQUIRED')
    return dict(process,token=token,host=socket.gethostname())


def owner_alive(owner):
    if not isinstance(owner,dict) or owner.get('host')!=socket.gethostname(): return None
    process=_process(owner.get('pid'))
    if process is False: return False
    if process is None: return None
    try:
        old=datetime.fromisoformat(owner['started_at'])
        actual=datetime.fromisoformat(process['started_at'])
        if old.tzinfo is None or actual.tzinfo is None: return None
        old=old.astimezone(timezone.utc); actual=actual.astimezone(timezone.utc)
        return old==actual
    except (ValueError,KeyError,TypeError):
        return None
