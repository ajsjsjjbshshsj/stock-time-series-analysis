"""Restart-safe coordinator; existing owned worker remains the sole executor."""
from datetime import datetime
import time
from tasks.contracts import digest
from tasks.source import read_calendar
from .gate import NightlyGate,due_session,TZ
from .repository import DailyRepository,cycles,runs
from .service import DailyService

TERMINAL={'FAILED','CANCELLED','INTERRUPTED'}


def prediction_outcome(job):
    if job['state']=='SUCCEEDED':
        index=(job.get('candidate') or {}).get('index',{});meta=index.get('metadata',{})
        if index.get('artifact_kind')=='result' and meta.get('status')=='COMPLETE' and meta.get('operation')=='predict':
            return dict(status='COMPLETE',result_id=job['id'],published=False,reason=None)
        return dict(status='BLOCKED',reason='RESULT_NOT_READY')
    return dict(status='BLOCKED',reason='PREDICTION_FAILED') if job['state'] in TERMINAL else {}


class DailyScheduler:
    def __init__(self,tasks,*,gate=None,service=None,clock=None):
        self.tasks=tasks;self.repo=DailyRepository(tasks.repo.engine)
        self.service=service or DailyService(tasks.repo.engine)
        self.gate=gate or NightlyGate(tasks.repo.engine);self.clock=clock or time.time

    def queue(self,actor,request,key):
        return self.tasks.create(actor,request,digest(key)[:32])

    def cycle(self,cycle):
        state=cycle['state'];day=cycle['day'];actor=cycle['actor_id'];key=dict(day=day,proof=cycle['proof_sha256'])
        self.service.admin(actor)
        if state.get('status') in ('READY','BLOCKED'):return cycle
        new=dict(state)
        if not state.get('build_task'):
            job=self.queue(actor,dict(kind='feature_build',nightly_task_id=cycle['proof']['task_id']),dict(key,step='build'))
            new.update(build_task=job['id'],status='BUILDING')
        else:
            build=self.tasks.get(state['build_task'])
            if build['state'] in TERMINAL:new.update(status='BLOCKED',reason='FEATURE_BUILD_FAILED')
            elif build['state']=='SUCCEEDED':
                if not state.get('activation_task'):
                    job=self.queue(actor,dict(kind='feature_activate',feature_task_id=build['id'],action='activate'),dict(key,step='activate'))
                    new.update(activation_task=job['id'],status='ACTIVATING')
                else:
                    activation=self.tasks.get(state['activation_task'])
                    if activation['state'] in TERMINAL:new.update(status='BLOCKED',reason='FEATURE_ACTIVATION_FAILED')
                    elif activation['state']=='SUCCEEDED':new.update(status='READY',build_id=activation['candidate']['build_id'])
        self.repo.update(cycles,day,state,new)
        return self.repo.cycle(day,cycle['proof'],actor)

    def advance(self,plan,day,now):
        run=self.repo.run(plan,day);state=run['state']
        if state.get('status') in ('COMPLETE','BLOCKED') or state.get('retry_at',0)>now:return
        new=dict(state)
        try:
            self.service.validate(plan)
            if state.get('prediction_task'):
                job=self.tasks.get(state['prediction_task'])
                new.update(prediction_outcome(job))
            else:
                proof=self.gate.current(day)
                cycle=self.cycle(self.repo.cycle(day,proof,plan['actor_id']))
                if cycle['state'].get('status')=='BLOCKED':new.update(status='BLOCKED',reason=cycle['state']['reason'])
                elif cycle['state'].get('status')=='READY':
                    # Explicit saved model + exact active feature pin, never train/import.
                    job=self.queue(plan['actor_id'],dict(kind='model_workflow',operation='predict',algorithm=plan['proof']['algorithm'],
                        model_id=plan['model_id'],start_date=day,end_date=day,feature_build_id=cycle['state']['build_id']),
                        dict(plan=plan['id'],day=day,proof=plan['proof_sha256'],step='predict'))
                    new.update(status='PREDICTING',prediction_task=job['id'])
                else:new.update(status=cycle['state'].get('status','WAITING_FEATURES'))
        except (ValueError,PermissionError):new.update(status='BLOCKED',reason='BOUNDARY_OR_ROLE_CHANGED')
        except Exception:new.update(status='WAITING_DEPENDENCY',reason='DEPENDENCY_NOT_READY',retry_at=now+60)
        self.repo.update(runs,run['id'],state,new)

    def tick(self):
        if not self.repo.ready():return  # Optional explicit migration; no implicit DDL.
        now=int(self.clock());clock=datetime.fromtimestamp(now,TZ);today=clock.date().isoformat()
        # Observing an already-submitted outcome is not permission to create
        # another stage. Finalize it even after disable/role loss or on closed
        # sessions, without depending on a current calendar or a free slot.
        for run in self.repo.recent():
            state=run['state']
            if state.get('status') in ('COMPLETE','BLOCKED'):continue
            if state.get('prediction_task'):
                outcome=prediction_outcome(self.tasks.get(state['prediction_task']))
                if outcome:self.repo.update(runs,run['id'],state,dict(state,**outcome))
                continue
            if run['day']<today:self.repo.update(runs,run['id'],state,dict(state,status='BLOCKED',reason='SESSION_EXPIRED'))
        slot=self.tasks.slot_status()
        if slot['quarantined'] or slot['task_id'] is not None:return
        enabled=[p for p in self.repo.list() if p['enabled']]
        if not enabled:return
        try:day=due_session(read_calendar(self.tasks.source.calendar_path),clock)
        except Exception:
            for plan in enabled:
                run=self.repo.run(plan,today);state=run['state']
                if state.get('status') not in ('COMPLETE','BLOCKED'):
                    self.repo.update(runs,run['id'],state,dict(state,status='WAITING_DEPENDENCY',reason='TRUSTED_CALENDAR_NOT_READY',retry_at=now+60))
            return
        if day is None:return
        for plan in enabled:
            # A fresh read means disabling a plan prevents subsequent stages.
            if self.repo.get(plan['id'])['enabled']:self.advance(plan,day,now)
