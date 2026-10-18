"""Persistent task operations. Current-role checks never trust a cached actor."""
import json
import time
from uuid import uuid4
from sqlalchemy import select
from api.auth.repository import users
from .repository import tasks,events
from .contracts import (Conflict,NotFound,TERMINAL,EVENTS,bound_integer,identity,
    idempotency,normalize_request,encoded,digest)


def public(row):
    result={key:row[key] for key in ('id','seq','actor_id','state','stage','created_at',
        'started_at','heartbeat_at','finished_at','failure_code')}
    result.update(request=json.loads(row['request']),binding=json.loads(row['binding']),
                  candidate=json.loads(row['candidate']) if row['candidate'] else None)
    return result


class TaskService:
    def __init__(self,repository,source,*,clock=None):
        self.repo=repository;self.source=source;self.clock=clock or time.time

    def _admin(self,c,actor):
        row=c.execute(select(users).where(users.c.id==actor).with_for_update()).mappings().one_or_none()
        if row is None or not row['enabled'] or row['role']!='admin' or row['must_change_password']:
            raise PermissionError('Access denied')

    def _row(self,c,task_id):
        identity(task_id)
        row=c.execute(select(tasks).where(tasks.c.id==task_id)).mappings().one_or_none()
        if row is None: raise NotFound('Task unavailable')
        return row

    def _event(self,c,task_id,event):
        if event not in EVENTS:raise ValueError('Invalid event')
        row=self._row(c,task_id)
        if row['event_count']>=2000:raise ValueError('Event bound exceeded')
        c.execute(events.insert().values(task_id=task_id,event=event,created_at=int(self.clock())))
        c.execute(tasks.update().where(tasks.c.id==task_id).values(event_count=row['event_count']+1))

    def create(self,actor_id,request,idempotency_key):
        request=normalize_request(request);key=idempotency(idempotency_key);request_hash=digest(request)
        with self.repo.transaction() as (c,_):
            self._admin(c,actor_id)
            old=c.execute(select(tasks).where(tasks.c.actor_id==actor_id,tasks.c.idempotency_key==key)).mappings().one_or_none()
            if old is not None:
                if old['request_hash']!=request_hash:raise Conflict('Idempotency conflict')
                return public(old)
            binding=self.source.bind(request)
            task_id=uuid4().hex
            c.execute(tasks.insert().values(id=task_id,actor_id=actor_id,idempotency_key=key,
                request_hash=request_hash,request=encoded(request),binding=encoded(binding),
                state='QUEUED',stage='QUEUED',created_at=int(self.clock()),event_count=0))
            self._event(c,task_id,'CREATED')
            return public(self._row(c,task_id))

    def get(self,task_id):
        with self.repo.engine.connect() as c:return public(self._row(c,task_id))

    def list(self,*,limit=20,before=None):
        bound_integer(limit)
        statement=select(tasks).order_by(tasks.c.seq.desc()).limit(limit+1)
        if before is not None:statement=statement.where(tasks.c.seq<bound_integer(before,1,2**63-1))
        with self.repo.engine.connect() as c:rows=c.execute(statement).mappings().all()
        return dict(items=[public(r) for r in rows[:limit]],
                    next_cursor=rows[limit-1]['seq'] if len(rows)>limit else None)

    def events(self,task_id,*,after=0,limit=100):
        bound_integer(after,0,2**63-1);bound_integer(limit)
        with self.repo.engine.connect() as c:
            self._row(c,task_id)
            rows=c.execute(select(events).where(events.c.task_id==task_id,events.c.id>after)
                           .order_by(events.c.id).limit(limit)).mappings().all()
        return dict(items=[dict(r) for r in rows],next_cursor=rows[-1]['id'] if rows else after)

    def cancel(self,actor_id,task_id):
        with self.repo.transaction() as (c,_):
            self._admin(c,actor_id);row=self._row(c,task_id)
            if row['state'] in TERMINAL or row['state']=='CANCEL_REQUESTED':return public(row)
            state='CANCELLED' if row['state']=='QUEUED' else 'CANCEL_REQUESTED'
            values=dict(state=state)
            if state=='CANCELLED':values['finished_at']=int(self.clock())
            c.execute(tasks.update().where(tasks.c.id==task_id).values(**values))
            self._event(c,task_id,state)
            return public(self._row(c,task_id))
