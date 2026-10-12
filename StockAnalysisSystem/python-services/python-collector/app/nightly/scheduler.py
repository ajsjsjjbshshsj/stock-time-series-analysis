"""Restart-safe 20:00 scheduling under the shared worker root lock.

This controller schedules dates, not models. A failed historical day remains
visible and does not prevent a later day's independent task from being queued.
"""
from datetime import datetime,timedelta,timezone
from pathlib import Path
import json,os,time,uuid

from app.preparation.contracts import TZ,identity,timestamp
from app.preparation.service import PreparationFailure
from app.preparation.worker import require_root_lock
from app.repositories.preparation_repository import ERRORS
from app.market_data.forward_snapshot import read
from .contracts import SCOPE,START,market_day,parse_run_at,due_days,is_a_share

FIELDS={'scope','start_date','run_at','blocked_error','retry_not_before','last_scan_at'}
WAIT={'MINUTE_QUOTA','HOURLY_QUOTA','QUOTA_COOLDOWN','REQUEST_STATE_BUSY'}

class NightlyScheduler:
    def __init__(self,tasks,calendar_provider,data_root,*,clock=time.time,run_at='20:00',bootstrap_start=None):
        parse_run_at(run_at)
        if bootstrap_start is not None:market_day(bootstrap_start)
        self.tasks=tasks;self.calendar_provider=calendar_provider;self.data_root=Path(data_root).resolve()
        self.clock=clock;self.run_at=run_at;self.bootstrap_start=bootstrap_start
        self.path=self.data_root/'nightly_scheduler_state.json'

    def _default(self):
        return dict(scope=SCOPE,start_date=None,run_at=self.run_at,blocked_error=None,retry_not_before=None,last_scan_at=None)

    def _load(self):
        if not self.path.exists():return self._default()
        if not self.path.resolve().is_relative_to(self.data_root):raise ValueError('CONTROL_OUTSIDE_ROOT')
        envelope=read(self.path)
        if (not isinstance(envelope,dict) or set(envelope)!={'schema_version','body','sha256'}
                or type(envelope['schema_version']) is not int or envelope['schema_version']!=1):
            raise ValueError('CONTROL_SCHEMA')
        body=envelope['body']
        if (not isinstance(body,dict) or set(body)!=FIELDS or body['scope']!=SCOPE or envelope['sha256']!=identity(body)
                or body['blocked_error'] is not None and body['blocked_error'] not in ERRORS|{'MIGRATION_REQUIRED'}):
            raise ValueError('CONTROL_BINDING_INVALID')
        parse_run_at(body['run_at'])
        if body['start_date'] is not None:market_day(body['start_date'])
        for field in ('retry_not_before','last_scan_at'):
            if body[field] is not None:timestamp(body[field])
        return body

    def _save(self,body):
        require_root_lock(self.data_root)
        if not self.path.resolve().is_relative_to(self.data_root):raise ValueError('CONTROL_OUTSIDE_ROOT')
        self.data_root.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        envelope=dict(schema_version=1,body=body,sha256=identity(body))
        try:
            with temporary.open('x',encoding='utf-8') as stream:
                json.dump(envelope,stream,ensure_ascii=False,sort_keys=True,allow_nan=False)
                stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,self.path)
        finally:
            if temporary.exists():temporary.unlink()

    def status(self):
        try:return self._load()
        except (ValueError,KeyError,TypeError,OSError):return dict(self._default(),blocked_error='SCHEMA')

    def reset_error(self):
        require_root_lock(self.data_root)
        body=self._load() # Invalid evidence is never silently replaced.
        body.update(blocked_error=None,retry_not_before=None);self._save(body)

    def next_delay(self):
        now=datetime.fromtimestamp(self.clock(),timezone.utc);local=now.astimezone(TZ)
        boundary=datetime.combine(local.date(),parse_run_at(self.run_at),TZ)
        if boundary<=local:boundary+=timedelta(days=1)
        choices=[60,(boundary-now).total_seconds()]
        retry=self.status()['retry_not_before']
        if retry is not None:choices.append(timestamp(retry).timestamp()-self.clock())
        return max(1,min(choices))

    def tick(self):
        require_root_lock(self.data_root)
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        try:body=self._load()
        except (ValueError,KeyError,TypeError,OSError):return dict(status='BLOCKED',blocked_error='SCHEMA')
        if body['blocked_error'] is not None:return dict(status='BLOCKED',**body)
        if body['retry_not_before'] is not None and timestamp(body['retry_not_before'])>now:
            return dict(status='WAITING_RETRY',**body)
        try:return self._tick(body,now)
        except PreparationFailure as error:kind=error.kind;deadline=error.retry_not_before
        except (ValueError,KeyError,TypeError,OSError):kind='SCHEMA';deadline=None
        except RuntimeError as error:
            if str(error).startswith('WORKER'):raise
            kind=str(error) if str(error) in ERRORS|{'MIGRATION_REQUIRED'} else 'STORAGE_UNAVAILABLE';deadline=None
        if kind in WAIT:
            until=deadline if type(deadline) in (int,float) and deadline>self.clock() else self.clock()+60
            body.update(blocked_error=None,retry_not_before=datetime.fromtimestamp(until,timezone.utc).isoformat())
        else:body.update(blocked_error=kind,retry_not_before=None)
        self._save(body)
        return dict(status='WAITING_RETRY' if kind in WAIT else 'BLOCKED',**body)

    def _tick(self,body,now):
        local=now.astimezone(TZ);at=parse_run_at(self.run_at)
        last=local.date() if local.time()>=at else local.date()-timedelta(days=1)
        earliest=self.tasks.earliest_date()
        starts=[s for s in (body['start_date'],self.bootstrap_start,earliest) if s is not None]
        if starts:start=min(starts)
        else:start=max(market_day(START),last-timedelta(days=365)).isoformat()
        if self.bootstrap_start is not None and market_day(self.bootstrap_start)>local.date():
            raise ValueError('BOOTSTRAP_IN_FUTURE')
        if market_day(start)>last:calendar=dict(source='tushare.trade_cal',rows=[]);days=[]
        else:
            calendar=self.calendar_provider(start,last.isoformat(),now)
            days=due_days(calendar,now,start,self.run_at)
        if not starts:
            if not days:raise ValueError('NO_DUE_SESSION_IN_LOOKBACK')
            start=days[-1];days=days[-1:]
        body.update(start_date=start,run_at=self.run_at,retry_not_before=None,last_scan_at=now.isoformat())
        self._save(body) # Persist the origin before submitting any date.
        existing={r['trade_date']:r for r in self.tasks.list_runs(start,days[-1])} if days else {}
        pending=[day for day in days if day not in existing]
        for day in pending[:31]:
            scheduled=datetime.combine(market_day(day),at,TZ).isoformat()
            self.tasks.submit(day,scheduled_for=scheduled,now=now.isoformat())
        runs={r['trade_date']:r for r in self.tasks.list_runs(start,days[-1])} if days else {}
        continuous=None
        for day in days:
            if day not in runs or runs[day]['status']!='SUCCEEDED':break
            continuous=day
        return dict(status='QUEUED' if pending else 'IDLE',enqueued=min(31,len(pending)),remaining_to_enqueue=max(0,len(pending)-31),
            continuous_through=continuous,**body)


class PolicyBoundPreparationService:
    """Nightly mode enforces scope without shrinking a fixed model universe."""
    def __init__(self,service):
        self.service=service;self.data_root=service.data_root

    def execute(self,task,*,owner_token):
        now=datetime.fromtimestamp(self.service.clock(),timezone.utc).isoformat()
        task=self.service.tasks.fence(task['task_id'],owner_token,now=now)
        pool=self.service.pools.resolve(pool_version=task['request']['pool_version'])
        if task['request']['requested_start']<START or any(not is_a_share(c) for c in pool['codes']):
            patch=dict(status='FAILED',error_kind='SCHEMA',updated_at=now,
                result=dict(data_ready=False,model_ready=False,issues={'POLICY_SCOPE_VIOLATION':1}))
            if not self.service.tasks.update(task['task_id'],owner_token,patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
            return self.service.tasks.get(task['task_id'])
        return self.service.execute(task,owner_token=owner_token)
