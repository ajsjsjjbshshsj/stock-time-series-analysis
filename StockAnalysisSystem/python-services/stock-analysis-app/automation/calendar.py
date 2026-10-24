"""Extend worker calendar from sealed collector rows only while the queue is idle."""
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
import hashlib,os
from sqlalchemy import select
from tasks.repository import tasks
from tasks.source import PROJECT,read_calendar
from tasks.artifacts import _is_link,write_json
from tasks.contracts import encoded


def refresh_calendar(service,now,*,data_root=None):
    from api.collector import collector_imports
    collector_imports()
    from app.preparation.calendar import CalendarStore
    from app.runtime_paths import RuntimePaths
    from .gate import TZ
    if now.utcoffset() is None:raise ValueError('Aware calendar clock required')
    today=now.astimezone(TZ).date().isoformat()
    root=data_root if data_root is not None else RuntimePaths.from_environment(PROJECT,os.environ).data_root
    cached=CalendarStore(root,None,None).cached()['rows']
    target=Path(service.source.calendar_path)
    if any(_is_link(p) for p in (target,*target.parents)):raise ValueError('Unsafe calendar path')
    with service.repo.transaction() as (connection,slot):
        # A calendar is a task input. Never change it under admitted work.
        if slot['task_id'] is not None or slot['quarantined']:return False
        if connection.execute(select(tasks.c.id).where(tasks.c.state.in_(['QUEUED','RUNNING','CANCEL_REQUESTED'])).limit(1)).first():return False
        original=target.read_bytes() if target.exists() else None
        previous=read_calendar(target) if original is not None else None
        start=previous['start'] if previous else '2023-01-01'
        rows={r['cal_date']:r['is_open'] for r in previous['rows']} if previous else {}
        for row in cached:
            day=row['cal_date']
            if not start<=day<=today:continue
            if day in rows and rows[day]!=row['is_open']:raise ValueError('Trusted calendar history changed')
            rows[day]=row['is_open']
        if not rows:return False
        end=max(rows)
        if previous and end<=previous['end']:return False
        first,last=date.fromisoformat(start),date.fromisoformat(end)
        expected=[str(first+timedelta(days=i)) for i in range((last-first).days+1)]
        if any(day not in rows for day in expected):raise ValueError('Complete calendar coverage required')
        body=dict(source='tushare.trade_cal',exchange='SSE',start=start,end=end,
            rows=[dict(cal_date=day,is_open=rows[day]) for day in expected])
        if len(encoded(body).encode('utf-8'))>256*1024:raise ValueError('Calendar limit exceeded')
        target.parent.mkdir(parents=True,exist_ok=True)
        if original is not None:
            history=target.parent/'calendar_history'
            if _is_link(history):raise ValueError('Unsafe calendar archive')
            history.mkdir(exist_ok=True)
            backup=history/(hashlib.sha256(original).hexdigest()+'.json')
            if _is_link(backup):raise ValueError('Unsafe calendar archive')
            if backup.exists():
                if backup.read_bytes()!=original:raise ValueError('Calendar archive changed')
            else:
                with backup.open('xb') as stream:stream.write(original);stream.flush();os.fsync(stream.fileno())
        temporary=target.with_name(target.name+'.'+uuid4().hex+'.tmp')
        try:
            write_json(temporary,body)
            read_calendar(temporary)
            if (target.read_bytes() if target.exists() else None)!=original:raise ValueError('Calendar changed during synchronization')
            os.replace(temporary,target)
        finally:
            if temporary.exists():temporary.unlink()
        return True
