"""Explicit migration and immutable activation intents, separate from market data."""
from sqlalchemy import MetaData,Table,Column,String,Text,Boolean,BigInteger,select,inspect
from sqlalchemy.exc import IntegrityError
from tasks.contracts import identity,digest,encoded
import json
from uuid import uuid4

metadata=MetaData()
activations=Table('analysis_feature_activation',metadata,
    Column('task_id',String(32),primary_key=True),Column('build_id',String(32),nullable=False),
    Column('previous_id',String(32)),Column('action',String(16),nullable=False),
    Column('identity_sha256',String(64),nullable=False),Column('state',String(16),nullable=False))
plans=Table('analysis_daily_plan',metadata,
    Column('id',String(32),primary_key=True),Column('actor_id',String(32),nullable=False),
    Column('model_id',String(32),nullable=False),Column('proof',Text,nullable=False),
    Column('proof_sha256',String(64),nullable=False),Column('enabled',Boolean,nullable=False),
    Column('created_at',BigInteger,nullable=False))
cycles=Table('analysis_daily_cycle',metadata,
    Column('day',String(10),primary_key=True),Column('actor_id',String(32),nullable=False),
    Column('proof',Text,nullable=False),Column('proof_sha256',String(64),nullable=False),Column('state',Text,nullable=False))
runs=Table('analysis_daily_run',metadata,
    Column('id',String(64),primary_key=True),Column('plan_id',String(32),nullable=False,index=True),
    Column('day',String(10),nullable=False),Column('state',Text,nullable=False))


def migrate(engine):metadata.create_all(engine)


def intent(value):
    result={k:value[k] for k in ('task_id','build_id','previous_id','action')}
    for key in ('task_id','build_id'):identity(result[key])
    if result['previous_id'] is not None:identity(result['previous_id'])
    if result['action'] not in ('activate','rollback'):raise ValueError('Invalid activation action')
    return result


def checked(row):
    row=dict(row)
    if row['identity_sha256']!=digest(intent(row)) or row['state'] not in ('PREPARED','COMMITTED','ABORTED'):
        raise ValueError('Activation intent changed')
    return row


def publication_pending(connection):
    if not inspect(connection).has_table(activations.name):return False
    return connection.execute(select(activations.c.task_id).where(activations.c.state=='PREPARED').limit(1)).first() is not None


def daily_row(row):
    if row is None:raise LookupError('Daily record unavailable')
    value=dict(row)
    for key in ('proof','state'):
        if key in value:value[key]=json.loads(value[key])
    if 'proof' in value and digest(value['proof'])!=value['proof_sha256']:raise ValueError('Daily proof changed')
    return value


class DailyRepository:
    def __init__(self,engine):self.engine=engine

    def ready(self):
        with self.engine.connect() as c:return all(inspect(c).has_table(t.name) for t in (plans,cycles,runs,activations))

    def create(self,actor,model,proof,now):
        identity(actor);identity(model);id=digest(dict(actor=actor,model=model))[:32]
        with self.engine.begin() as c:
            old=c.execute(select(plans).where(plans.c.id==id)).mappings().one_or_none()
            if old is not None:
                if daily_row(old)['proof']!=proof:raise ValueError('Daily model proof changed')
                return daily_row(old)
            if len(c.execute(select(plans.c.id).limit(101)).fetchall())>=100:raise ValueError('Daily plan limit reached')
            c.execute(plans.insert().values(id=id,actor_id=actor,model_id=model,proof=encoded(proof),proof_sha256=digest(proof),enabled=False,created_at=now))
        return self.get(id)

    def get(self,id):
        identity(id)
        with self.engine.connect() as c:return daily_row(c.execute(select(plans).where(plans.c.id==id)).mappings().one_or_none())

    def list(self):
        with self.engine.connect() as c:return [daily_row(r) for r in c.execute(select(plans).order_by(plans.c.created_at.desc(),plans.c.id).limit(100)).mappings()]

    def enable(self,id,enabled):
        identity(id)
        if type(enabled) is not bool:raise ValueError('Boolean enabled required')
        with self.engine.begin() as c:c.execute(plans.update().where(plans.c.id==id).values(enabled=enabled))
        return self.get(id)

    def cycle(self,day,proof,actor):
        try:
            with self.engine.begin() as c:
                old=c.execute(select(cycles).where(cycles.c.day==day)).mappings().one_or_none()
                if old is None:c.execute(cycles.insert().values(day=day,actor_id=actor,proof=encoded(proof),proof_sha256=digest(proof),state='{}'))
        except IntegrityError:pass  # Another supervisor won the same daily identity.
        with self.engine.connect() as c:value=daily_row(c.execute(select(cycles).where(cycles.c.day==day)).mappings().one())
        if value['proof']!=proof:raise ValueError('Nightly daily proof changed')
        return value

    def run(self,plan,day):
        id=digest(dict(plan=plan['id'],day=day,model=plan['model_id'],proof=plan['proof_sha256']))
        try:
            with self.engine.begin() as c:
                if c.execute(select(runs.c.id).where(runs.c.id==id)).first() is None:
                    c.execute(runs.insert().values(id=id,plan_id=plan['id'],day=day,state='{}'))
        except IntegrityError:pass
        with self.engine.connect() as c:return daily_row(c.execute(select(runs).where(runs.c.id==id)).mappings().one())

    def update(self,table,key,old,new):
        # Compare-and-swap prevents a late supervisor overwriting a newer stage.
        col=cycles.c.day if table is cycles else runs.c.id
        with self.engine.begin() as c:
            return c.execute(table.update().where(col==key,table.c.state==encoded(old)).values(state=encoded(new))).rowcount==1

    def recent(self):
        with self.engine.connect() as c:return [daily_row(r) for r in c.execute(select(runs).order_by(runs.c.day.desc(),runs.c.id).limit(100)).mappings()]


class ActivationJournal:
    def __init__(self,engine):self.engine=engine

    def get(self,id):
        identity(id)
        with self.engine.connect() as c:
            row=c.execute(select(activations).where(activations.c.task_id==id)).mappings().one_or_none()
        if row is None:raise LookupError('Activation intent unavailable')
        return checked(row)

    def pending(self):
        with self.engine.connect() as c:
            rows=c.execute(select(activations).where(activations.c.state=='PREPARED').limit(101)).mappings().all()
        if len(rows)>100:raise ValueError('Too many unresolved activation intents')
        return [checked(row) for row in rows]

    def prepare(self,value):
        value=intent(value);sha=digest(value)
        with self.engine.begin() as c:
            old=c.execute(select(activations).where(activations.c.task_id==value['task_id']).with_for_update()).mappings().one_or_none()
            if old is not None:
                if checked(old)['identity_sha256']!=sha or old['state']!='PREPARED':raise ValueError('Activation intent conflict')
                return
            c.execute(activations.insert().values(**value,identity_sha256=sha,state='PREPARED'))

    def finish(self,value,state):
        value=intent(value)
        if state not in ('COMMITTED','ABORTED'):raise ValueError('Invalid activation outcome')
        with self.engine.begin() as c:
            old=c.execute(select(activations).where(activations.c.task_id==value['task_id']).with_for_update()).mappings().one()
            if checked(old)['identity_sha256']!=digest(value) or old['state'] not in ('PREPARED',state):
                raise ValueError('Activation outcome conflict')
            c.execute(activations.update().where(activations.c.task_id==value['task_id']).values(state=state))
