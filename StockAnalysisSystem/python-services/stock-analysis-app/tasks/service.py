"""Persistent task operations. Current-role checks never trust a cached actor."""
import json
import time
from uuid import uuid4
from sqlalchemy import select
from api.auth.repository import users
from .repository import tasks,events,slots
from .contracts import (Conflict,NotFound,LeaseLost,TaskFailure,TERMINAL,EVENTS,STAGES,bound_integer,identity,
    idempotency,normalize_request,encoded,digest)


def public(row):
    result={key:row[key] for key in ('id','seq','actor_id','state','stage','created_at',
        'started_at','heartbeat_at','finished_at','failure_code')}
    result.update(request=json.loads(row['request']),binding=json.loads(row['binding']),
                  candidate=json.loads(row['candidate']) if row['candidate'] else None)
    if result['request']['kind'] in ('lightgbm_all_market','model_workflow'):
        binding=result['binding']
        result['binding']={k:binding[k] for k in ('build_id','pool_count','pool_identity','source_sha256','source_start','source_cutoff') if k in binding}
        result['progress']=json.loads(row['progress']) if row['progress'] else None
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

    def _event(self,c,task_id,event,*,actor_id=None,detail=None):
        if event not in EVENTS:raise ValueError('Invalid event')
        row=self._row(c,task_id)
        if row['event_count']>=2000:raise ValueError('Event bound exceeded')
        c.execute(events.insert().values(task_id=task_id,event=event,created_at=int(self.clock()),actor_id=actor_id,
                                        detail=encoded(detail) if detail is not None else None))
        c.execute(tasks.update().where(tasks.c.id==task_id).values(event_count=row['event_count']+1))

    def create(self,actor_id,request,idempotency_key):
        # MySQL installations may use case-insensitive collations. Hash opaque
        # keys so A... and a... retain distinct identities on every supported DB.
        request=normalize_request(request);key=digest(idempotency(idempotency_key));request_hash=digest(request)
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
            self._event(c,task_id,'CREATED',actor_id=actor_id)
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
        items=[dict(r) for r in rows]
        for item in items:
            if item['detail'] is not None:item['detail']=json.loads(item['detail'])
        return dict(items=items,next_cursor=rows[-1]['id'] if rows else after)

    def cancel(self,actor_id,task_id):
        with self.repo.transaction() as (c,_):
            self._admin(c,actor_id);row=self._row(c,task_id)
            if row['state'] in TERMINAL or row['state']=='CANCEL_REQUESTED':return public(row)
            state='CANCELLED' if row['state']=='QUEUED' else 'CANCEL_REQUESTED'
            values=dict(state=state)
            if state=='CANCELLED':values['finished_at']=int(self.clock())
            c.execute(tasks.update().where(tasks.c.id==task_id).values(**values))
            self._event(c,task_id,state,actor_id=actor_id)
            return public(self._row(c,task_id))

    def _expire(self,c,slot):
        if slot['task_id'] and not slot['quarantined'] and slot['lease_until']<=int(self.clock()):
            row=self._row(c,slot['task_id'])
            if row['state'] not in TERMINAL:
                c.execute(tasks.update().where(tasks.c.id==row['id']).values(state='INTERRUPTED',
                    stage='DONE',failure_code='INTERRUPTED',finished_at=int(self.clock())))
                self._event(c,row['id'],'INTERRUPTED')
            c.execute(slots.update().where(slots.c.id==1).values(quarantined=True))
            return True
        return bool(slot['quarantined'])

    def expire(self):
        with self.repo.transaction() as (c,slot):return self._expire(c,slot)

    def slot_status(self):
        with self.repo.engine.connect() as c:
            row=c.execute(select(slots).where(slots.c.id==1)).mappings().one()
            return dict(row)

    def claim(self,worker_id):
        identity(worker_id)
        with self.repo.transaction() as (c,slot):
            if self._expire(c,slot) or slot['task_id']:return None
            row=c.execute(select(tasks).where(tasks.c.state=='QUEUED').order_by(tasks.c.seq).limit(1)).mappings().one_or_none()
            if row is None:return None
            generation=slot['generation']+1;now=int(self.clock())
            c.execute(slots.update().where(slots.c.id==1).values(task_id=row['id'],worker_id=worker_id,
                generation=generation,lease_until=now+30,quarantined=False))
            c.execute(tasks.update().where(tasks.c.id==row['id']).values(state='RUNNING',stage='PREFLIGHT',
                worker_id=worker_id,generation=generation,started_at=now,heartbeat_at=now))
            self._event(c,row['id'],'PREFLIGHT')
            current=self._row(c,row['id'])
            result=public(current)
            result['binding']=json.loads(current['binding'])
            result.update(worker_id=worker_id,generation=generation)
            return result

    def execution_task(self,task_id,worker_id,generation,expected_sha):
        """Internal reference handshake: never exposed as a browser endpoint."""
        from .artifacts import task_digest
        with self.repo.transaction() as (c,slot):
            row=self._owned(c,slot,task_id,worker_id,generation)
            if row['state']!='RUNNING':raise LeaseLost('Task execution cancelled')
            task=dict(id=row['id'],request=json.loads(row['request']),binding=json.loads(row['binding']))
            if not isinstance(expected_sha,str) or task_digest(task)!=expected_sha:
                raise ValueError('Task reference mismatch')
            return task

    def _owned(self,c,slot,task_id,worker_id,generation):
        identity(task_id);identity(worker_id);bound_integer(generation,1,2**63-1)
        if (slot['task_id']!=task_id or slot['worker_id']!=worker_id or slot['generation']!=generation
                or slot['quarantined'] or slot['lease_until']<=int(self.clock())):
            raise LeaseLost('Execution lease unavailable')
        row=self._row(c,task_id)
        if row['state'] not in ('RUNNING','CANCEL_REQUESTED'):raise LeaseLost('Task not owned')
        return row

    def heartbeat(self,task_id,worker_id,generation):
        with self.repo.transaction() as (c,slot):
            row=self._owned(c,slot,task_id,worker_id,generation);now=int(self.clock())
            c.execute(slots.update().where(slots.c.id==1).values(lease_until=now+30))
            c.execute(tasks.update().where(tasks.c.id==task_id).values(heartbeat_at=now))
            return public(self._row(c,task_id))

    def stage(self,task_id,worker_id,generation,stage):
        allowed=('PREFLIGHT','INPUT','PROBE','TRAIN','VERIFY','SAVE')
        if stage not in allowed:raise ValueError('Invalid training stage')
        with self.repo.transaction() as (c,slot):
            row=self._owned(c,slot,task_id,worker_id,generation)
            if allowed.index(stage)<allowed.index(row['stage']):raise Conflict('Stage regression')
            if row['stage']!=stage:
                c.execute(tasks.update().where(tasks.c.id==task_id).values(stage=stage))
                self._event(c,task_id,stage)
            return public(self._row(c,task_id))

    def progress(self,task_id,worker_id,generation,phase,completed,total):
        if phase not in ('INPUT','PROBE','TRAIN'):raise ValueError('Invalid progress phase')
        bound_integer(total,1,10000000);bound_integer(completed,0,total)
        detail=dict(phase=phase,completed=completed,total=total)
        with self.repo.transaction() as (c,slot):
            row=self._owned(c,slot,task_id,worker_id,generation)
            if json.loads(row['request'])['kind'] not in ('lightgbm_all_market','model_workflow') or row['stage']!=phase:
                raise Conflict('Progress does not match current stage')
            old=json.loads(row['progress']) if row['progress'] else None
            if old and old['phase']==phase:
                if total!=old['total'] or completed<old['completed']:raise Conflict('Progress regression')
                if completed==old['completed']:return public(row)
            c.execute(tasks.update().where(tasks.c.id==task_id).values(progress=encoded(detail)))
            self._event(c,task_id,'PROGRESS',detail=detail)
            return public(self._row(c,task_id))

    def _release(self,c):
        c.execute(slots.update().where(slots.c.id==1).values(task_id=None,worker_id=None,lease_until=None,quarantined=False))

    def finish(self,task_id,worker_id,generation,outcome,*,process_stopped,candidate=None,failure_code=None):
        if process_stopped is not True:raise Conflict('Owned process tree must be stopped')
        if outcome not in ('SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'):raise ValueError('Invalid outcome')
        with self.repo.transaction() as (c,slot):
            row=self._owned(c,slot,task_id,worker_id,generation)
            if row['state']=='CANCEL_REQUESTED':outcome='CANCELLED'
            if outcome=='SUCCEEDED':
                request=json.loads(row['request'])
                if request['kind']=='model_workflow':
                    from workflows.contracts import validate_summary
                    from workflows.repository import WorkflowRepository
                    validate_summary(candidate)
                    if candidate['task_id']!=task_id:raise ValueError('Workflow identity mismatch')
                    WorkflowRepository(self.repo.engine).register(c,candidate)
                elif request['kind']=='lightgbm_all_market':
                    from .lightgbm_artifacts import validate_summary
                    validate_summary(candidate,dict(id=task_id,request=request,binding=json.loads(row['binding'])))
                else:self._validate_xgboost_candidate(candidate,task_id)
            else:candidate=None
            c.execute(tasks.update().where(tasks.c.id==task_id).values(state=outcome,stage='DONE',
                finished_at=int(self.clock()),candidate=encoded(candidate) if candidate else None,
                failure_code=TaskFailure(failure_code).code if outcome in ('FAILED','INTERRUPTED') else None))
            self._event(c,task_id,outcome);self._release(c)
            return public(self._row(c,task_id))

    @staticmethod
    def _validate_xgboost_candidate(candidate,task_id):
        import re
        if (not isinstance(candidate,dict) or set(candidate)!={'task_id','purpose','verified','manifest_sha256','files','metrics'}
                or candidate['task_id']!=task_id or candidate['purpose']!='engineering_smoke' or candidate['verified'] is not True
                or not isinstance(candidate['manifest_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',candidate['manifest_sha256'])
                or set(candidate['files'])!={'model.json','metadata.json','probe.json'}
                or any(not isinstance(v,str) or not re.fullmatch('[0-9a-f]{64}',v) for v in candidate['files'].values())):
            raise ValueError('Verified candidate required')

    def recover(self,actor_id,task_id,worker_id,generation,*,operator_confirmed):
        identity(task_id);identity(worker_id)
        with self.repo.transaction() as (c,slot):
            self._admin(c,actor_id)
            if (operator_confirmed is not True or not slot['quarantined'] or slot['task_id']!=task_id
                    or slot['worker_id']!=worker_id or slot['generation']!=generation):
                raise Conflict('Original process shutdown confirmation required')
            self._release(c);self._event(c,task_id,'RECOVERED',actor_id=actor_id)
