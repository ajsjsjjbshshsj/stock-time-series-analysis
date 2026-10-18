"""Explicit task schema and one independent execution-slot transaction lock."""
from contextlib import contextmanager
from sqlalchemy import (MetaData,Table,Column,Integer,BigInteger,String,Text,Boolean,
    UniqueConstraint,ForeignKey,select,inspect)
from sqlalchemy.dialects.mysql import LONGTEXT

schema=MetaData()
slots=Table('analysis_training_slot',schema,
    Column('id',Integer,primary_key=True),Column('task_id',String(32)),
    Column('worker_id',String(32)),Column('generation',BigInteger,nullable=False),
    Column('lease_until',BigInteger),Column('quarantined',Boolean,nullable=False))
tasks=Table('analysis_training_task',schema,
    Column('seq',Integer,primary_key=True,autoincrement=True),Column('id',String(32),nullable=False,unique=True),
    Column('actor_id',String(32),nullable=False),Column('idempotency_key',String(64),nullable=False),
    Column('request_hash',String(64),nullable=False),Column('request',Text,nullable=False),
    Column('binding',Text().with_variant(LONGTEXT(),'mysql'),nullable=False),Column('state',String(24),nullable=False,index=True),
    Column('stage',String(16),nullable=False),Column('created_at',BigInteger,nullable=False),
    Column('started_at',BigInteger),Column('heartbeat_at',BigInteger),Column('finished_at',BigInteger),
    Column('worker_id',String(32)),Column('generation',BigInteger),Column('failure_code',String(32)),
    Column('candidate',Text),Column('event_count',Integer,nullable=False),Column('progress',Text),
    UniqueConstraint('actor_id','idempotency_key',name='uq_training_actor_key'))
events=Table('analysis_training_event',schema,
    Column('id',Integer,primary_key=True,autoincrement=True),
    Column('task_id',String(32),ForeignKey('analysis_training_task.id'),nullable=False,index=True),
    Column('event',String(24),nullable=False),Column('created_at',BigInteger,nullable=False),
    Column('actor_id',String(32)),Column('detail',Text))


class TaskRepository:
    def __init__(self,engine): self.engine=engine

    def migrate(self):
        schema.create_all(self.engine)
        with self.engine.begin() as c:
            if c.execute(select(slots.c.id).where(slots.c.id==1)).first() is None:
                c.execute(slots.insert().values(id=1,generation=0,quarantined=False))

    def check_ready(self):
        """Fail startup once on an incomplete schema, never silently retry it."""
        with self.transaction() as (c,_):
            for table in schema.sorted_tables:c.execute(select(table).limit(0))
            if self.engine.dialect.name=='mysql':
                binding=next(x for x in inspect(c).get_columns(tasks.name) if x['name']=='binding')
                if not isinstance(binding['type'],LONGTEXT):raise RuntimeError('Explicit all-market migration required')

    def migrate_all_market(self):
        """Operator-only additive upgrade. Caller must stop workers/API first."""
        self.migrate()
        with self.transaction() as (c,slot):
            if slot['task_id'] is not None:raise RuntimeError('Training must be idle before upgrade')
            columns={x['name']:x for x in inspect(c).get_columns(tasks.name)}
            if self.engine.dialect.name=='mysql' and not isinstance(columns['binding']['type'],LONGTEXT):
                c.exec_driver_sql('ALTER TABLE analysis_training_task MODIFY COLUMN binding LONGTEXT NOT NULL')
            if 'progress' not in columns:
                c.exec_driver_sql('ALTER TABLE analysis_training_task ADD COLUMN progress TEXT NULL')
            if 'detail' not in {x['name'] for x in inspect(c).get_columns(events.name)}:
                c.exec_driver_sql('ALTER TABLE analysis_training_event ADD COLUMN detail TEXT NULL')
        self.check_ready()

    @contextmanager
    def transaction(self):
        with self.engine.begin() as c:
            slot=c.execute(select(slots).where(slots.c.id==1).with_for_update()).mappings().one_or_none()
            if slot is None: raise RuntimeError('Explicit training migration required')
            yield c,slot
