"""Fixed-pool preparation inbox. Explicit DDL, no provider or model operations."""
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import MetaData,Table,Column,String,Integer,UniqueConstraint,select,func,text
from sqlalchemy.exc import IntegrityError
from app.preparation.contracts import identity,validate_pool
from ._preparation_sql import transaction,stamp
from .web_collection_repository import identifier,date_range,page,CollectionCommands
from .market_update_repository import _task as validated_market_task

metadata=MetaData()
commands=Table('market_pool_preparation_request',metadata,
    Column('id',String(32),primary_key=True),Column('actor_id',String(32),nullable=False),
    Column('request_key',String(32),nullable=False),Column('request_sha256',String(64),nullable=False),
    Column('pool_version',String(40),nullable=False),Column('pool_sha256',String(64),nullable=False),
    Column('registry_stock_count',Integer,nullable=False),Column('start_date',String(10),nullable=False),
    Column('end_date',String(10),nullable=False),Column('status',String(24),nullable=False,index=True),
    Column('batch_id',String(40)),Column('planned_task_count',Integer),Column('error_kind',String(48)),Column('retry_not_before',String(32)),
    Column('created_at',String(32),nullable=False),Column('updated_at',String(32),nullable=False),
    UniqueConstraint('actor_id','request_key',name='uq_pool_preparation_actor_key'))
links=Table('market_pool_preparation_day',metadata,Column('request_id',String(32),primary_key=True),Column('task_id',String(32),primary_key=True))
STATES={'QUEUED','PLANNING','SUBMITTED','WAITING_RETRY','FAILED'}


def migrate(engine):
    """Only the explicit operator migration command calls this."""
    metadata.create_all(engine)


def payload(row):
    return {k:row[k] for k in ('pool_version','pool_sha256','registry_stock_count','start_date','end_date')}


class PreparationCommands:
    def __init__(self,factory):self.factory=factory

    def _row(self,session,id):
        row=session.execute(select(commands).where(commands.c.id==identifier(id))).mappings().first()
        if row is None:raise LookupError('PREPARATION_NOT_FOUND')
        row=dict(row)
        if row['request_sha256']!=identity(payload(row)) or row['status'] not in STATES:
            raise RuntimeError('PREPARATION_IDENTITY_CHANGED')
        return row

    def _get(self,session,id):
        row=self._row(session,id)
        count=session.execute(select(func.count()).select_from(links).where(links.c.request_id==id)).scalar_one()
        children=session.execute(text('SELECT r.* '
            'FROM market_pool_preparation_day d JOIN market_update_run r ON r.task_id=d.task_id '
            'WHERE d.request_id=:id'),dict(id=id)).mappings().all()
        if len(children)!=count:raise RuntimeError('PREPARATION_CHILD_MISSING')
        counts={}
        for child in children:
            self._validate_child(row,child)
            counts[child['status']]=counts.get(child['status'],0)+1
        if row['status']=='SUBMITTED' and (row['planned_task_count'] is None or count!=row['planned_task_count']):
            raise RuntimeError('PREPARATION_EXPECTED_CHILDREN_MISSING')
        state='NOT_STARTED'
        if row['status']=='SUBMITTED':
            state=('NO_APPLICABLE_SESSIONS' if count==0 else 'READY_CORE' if counts.get('SUCCEEDED',0)==count
                else 'NEEDS_CHECK' if counts.get('PARTIAL',0) or counts.get('FAILED',0) else 'RUNNING')
        elif row['status']=='FAILED':state='FAILED'
        row.update(task_count=count,task_states=counts,data_state=state,model_ready=False)
        # Internal request identity is never a path or a provider response.
        return row

    @staticmethod
    def _validate_child(parent,row):
        try:
            task=validated_market_task(row)
            if (parent['batch_id'] is None or task['requirements_version']!=parent['batch_id']
                    or not parent['start_date']<=task['trade_date']<=parent['end_date']):
                raise ValueError('OUTSIDE_BINDING')
        except (ValueError,TypeError,KeyError):
            raise RuntimeError('PREPARATION_CHILD_BINDING_CHANGED') from None

    def get(self,id):
        identifier(id)
        with transaction(self.factory) as session:return self._get(session,id)

    def submit(self,actor,key,pool,start,end,*,now=None):
        identifier(actor);identifier(key);validate_pool(pool)
        now=now or datetime.now(timezone.utc);date_range(start,end,now)
        value=dict(pool_version=pool['pool_version'],pool_sha256=identity(pool),registry_stock_count=pool['stock_count'],start_date=start,end_date=end)
        sha=identity(value)
        def previous(session):
            row=session.execute(select(commands.c.id,commands.c.request_sha256).where(commands.c.actor_id==actor,commands.c.request_key==key)).first()
            if row is not None:
                if row.request_sha256!=sha:raise ValueError('IDEMPOTENCY_CONFLICT')
                return self._get(session,row.id)
        collided=False
        with transaction(self.factory) as session:
            old=previous(session)
            if old is not None:return old
            id=uuid4().hex
            try:
                with session.begin_nested():
                    session.execute(commands.insert().values(id=id,actor_id=actor,request_key=key,request_sha256=sha,
                        **value,status='QUEUED',created_at=stamp(now),updated_at=stamp(now)))
            except IntegrityError:collided=True
            if not collided:return self._get(session,id)
        # Leave a MySQL REPEATABLE READ snapshot before reading a concurrent winner.
        with transaction(self.factory) as session:
            result=previous(session)
            if result is None:raise RuntimeError('STORAGE_UNAVAILABLE')
            return result

    def list(self,limit=20,offset=0):
        page(limit,offset)
        with transaction(self.factory) as session:
            ids=session.execute(select(commands.c.id).order_by(commands.c.created_at.desc(),commands.c.id).limit(limit).offset(offset)).scalars().all()
            total=session.execute(select(func.count()).select_from(commands)).scalar_one()
            return dict(items=[self._get(session,id) for id in ids],total=total,offset=offset)

    def pending(self,now):
        with transaction(self.factory) as session:
            id=session.execute(select(commands.c.id).where(commands.c.status.in_(['QUEUED','PLANNING','WAITING_RETRY']),
                commands.c.retry_not_before.is_(None)|(commands.c.retry_not_before<=stamp(now)))
                .order_by(commands.c.created_at,commands.c.id).limit(1).with_for_update()).scalar_one_or_none()
            if id is None:return None
            row=self._row(session,id)
            session.execute(commands.update().where(commands.c.id==id).values(status='PLANNING',updated_at=stamp(now)))
            return dict(row,status='PLANNING')

    def patch(self,id,*,now,**values):
        if not values or not set(values)<={'status','batch_id','planned_task_count','error_kind','retry_not_before'}:
            raise ValueError('INVALID_PREPARATION_PATCH')
        if 'status' in values and values['status'] not in STATES:raise ValueError('INVALID_PREPARATION_STATE')
        if 'planned_task_count' in values and (type(values['planned_task_count']) is not int or not 0<=values['planned_task_count']<=10000):
            raise ValueError('INVALID_PREPARATION_EXPECTED_COUNT')
        with transaction(self.factory) as session:
            old=self._row(session,id)
            if old['batch_id'] is not None and values.get('batch_id',old['batch_id'])!=old['batch_id']:
                raise ValueError('PREPARATION_BATCH_IMMUTABLE')
            if old['planned_task_count'] is not None and values.get('planned_task_count',old['planned_task_count'])!=old['planned_task_count']:
                raise ValueError('PREPARATION_EXPECTED_COUNT_IMMUTABLE')
            session.execute(commands.update().where(commands.c.id==id).values(**values,updated_at=stamp(now)))

    def link(self,id,task_id):
        identifier(id);identifier(task_id)
        with transaction(self.factory) as session:
            command=self._row(session,id)
            task=session.execute(text('SELECT * FROM market_update_run WHERE task_id=:id'),dict(id=task_id)).mappings().first()
            if (task is None or command['batch_id'] is None or task['requirements_version']!=command['batch_id']
                    or not command['start_date']<=str(task['trade_date'])<=command['end_date']):
                raise ValueError('PREPARATION_CHILD_OUTSIDE_BINDING')
            self._validate_child(command,task)
            if session.execute(select(links).where(links.c.request_id==id,links.c.task_id==task_id)).first() is None:
                session.execute(links.insert().values(request_id=id,task_id=task_id))

    def children(self,id,limit=20,offset=0):
        page(limit,offset)
        with transaction(self.factory) as session:
            parent=self._get(session,id)
            ids=session.execute(select(links.c.task_id).where(links.c.request_id==id).order_by(links.c.task_id).limit(limit).offset(offset)).scalars().all()
            return dict(items=[CollectionCommands._task(session,child) for child in ids],total=parent['task_count'],offset=offset)

    def retry(self,id,*,now=None):
        identifier(id);now=now or datetime.now(timezone.utc)
        with transaction(self.factory) as session:
            row=self._row(session,id)
            if row['status'] in ('QUEUED','PLANNING'):return self._get(session,id)
            if row['status'] not in ('FAILED','WAITING_RETRY'):raise ValueError('PREPARATION_NOT_RETRYABLE')
            session.execute(commands.update().where(commands.c.id==id,commands.c.status.in_(['FAILED','WAITING_RETRY']))
                .values(status='QUEUED',error_kind=None,retry_not_before=None,updated_at=stamp(now)))
            return self._get(session,id)

    def child(self,id,task_id):
        identifier(id);identifier(task_id)
        with transaction(self.factory) as session:
            parent=self._row(session,id)
            if session.execute(select(links).where(links.c.request_id==id,links.c.task_id==task_id)).first() is None:
                raise LookupError('PREPARATION_CHILD_NOT_FOUND')
            task=session.execute(text('SELECT * FROM market_update_run WHERE task_id=:id'),dict(id=task_id)).mappings().first()
            if task is None:raise RuntimeError('PREPARATION_CHILD_BINDING_CHANGED')
            self._validate_child(parent,task)
            return CollectionCommands._task(session,task_id)
