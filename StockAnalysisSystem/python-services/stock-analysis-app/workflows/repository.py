"""Explicit additive schema and audited visibility, not a quality/profit gate."""
import json,time
from sqlalchemy import MetaData,Table,Column,Integer,String,Text,Boolean,BigInteger,select
from api.auth.repository import users,control
from tasks.contracts import identity,bound_integer,encoded,Conflict,NotFound
from .contracts import validate_summary

schema=MetaData()
models=Table('analysis_workflow_model',schema,
    Column('seq',Integer,primary_key=True,autoincrement=True),Column('id',String(32),nullable=False,unique=True),
    Column('algorithm',String(16),nullable=False),Column('manifest_sha256',String(64),nullable=False),
    Column('title',String(120),nullable=False),Column('created_at',BigInteger,nullable=False),Column('metadata',Text,nullable=False))
results=Table('analysis_workflow_result',schema,
    Column('seq',Integer,primary_key=True,autoincrement=True),Column('id',String(32),nullable=False,unique=True),
    Column('model_id',String(32),nullable=False,index=True),Column('algorithm',String(16),nullable=False),
    Column('manifest_sha256',String(64),nullable=False),Column('title',String(120),nullable=False),
    Column('created_at',BigInteger,nullable=False),Column('metadata',Text,nullable=False),
    Column('published',Boolean,nullable=False),Column('published_at',BigInteger))
publication_audit=Table('analysis_workflow_publication',schema,
    Column('seq',Integer,primary_key=True,autoincrement=True),Column('actor_id',String(32),nullable=False),
    Column('result_id',String(32),nullable=False),Column('published',Boolean,nullable=False),Column('created_at',BigInteger,nullable=False))


def record(row):
    if row is None:return None
    value=dict(row);value['metadata']=json.loads(value['metadata']);return value


class WorkflowRepository:
    def __init__(self,engine):self.engine=engine

    def migrate(self):schema.create_all(self.engine)

    def check_ready(self):
        with self.engine.connect() as c:
            for table in schema.sorted_tables:c.execute(select(table).limit(0))

    def register(self,c,summary):
        validate_summary(summary);index=summary['index'];id=summary['task_id']
        table=models if index['artifact_kind']=='model' else results
        old=c.execute(select(table).where(table.c.id==id)).mappings().one_or_none()
        if old:
            if (old['manifest_sha256']!=summary['manifest_sha256'] or old['metadata']!=encoded(index['metadata'])
                    or old['algorithm']!=index['algorithm']):raise Conflict('Workflow index changed')
            return
        values=dict(id=id,algorithm=index['algorithm'],manifest_sha256=summary['manifest_sha256'],
            title=index['title'],created_at=index['created_at'],metadata=encoded(index['metadata']))
        if table is results:
            parent=c.execute(select(models.c.id).where(models.c.id==index['model_id'])).scalar_one_or_none()
            if parent is None:raise ValueError('Registered parent model required')
            values.update(model_id=index['model_id'],published=False)
        c.execute(table.insert().values(**values))

    def _list(self,table,limit,before,public=False):
        bound_integer(limit,1,100);query=select(table).order_by(table.c.seq.desc()).limit(limit+1)
        if before is not None:query=query.where(table.c.seq<bound_integer(before,1,2**63-1))
        if public:query=query.where(results.c.published==True)
        with self.engine.connect() as c:rows=c.execute(query).mappings().all()
        return dict(items=[record(r) for r in rows[:limit]],next_cursor=rows[limit-1]['seq'] if len(rows)>limit else None)

    def models(self,limit=20,before=None):return self._list(models,limit,before)

    def results(self,limit=20,before=None,public=False):return self._list(results,limit,before,public)

    def _get(self,table,id,public=False):
        identity(id);query=select(table).where(table.c.id==id)
        if public:query=query.where(results.c.published==True)
        with self.engine.connect() as c:return record(c.execute(query).mappings().one_or_none())

    def get_model(self,id):return self._get(models,id)

    def get_result(self,id,public=False):return self._get(results,id,public)

    def set_publication(self,actor,id,published):
        identity(actor);identity(id)
        if type(published) is not bool:raise ValueError('Publication flag required')
        with self.engine.begin() as c:
            if c.execute(select(control.c.id).where(control.c.id==1).with_for_update()).first() is None:
                raise RuntimeError('Explicit auth migration required')
            user=c.execute(select(users).where(users.c.id==actor).with_for_update()).mappings().one_or_none()
            if user is None or not user['enabled'] or user['role']!='admin' or user['must_change_password']:
                raise PermissionError('Current administrator required')
            row=c.execute(select(results).where(results.c.id==id).with_for_update()).mappings().one_or_none()
            if row is None:raise NotFound('Result not found')
            if published and json.loads(row['metadata']).get('status')!='COMPLETE':raise ValueError('Result not complete')
            if row['published']!=published:
                now=int(time.time())
                c.execute(results.update().where(results.c.id==id).values(published=published,published_at=now if published else None))
                c.execute(publication_audit.insert().values(actor_id=actor,result_id=id,published=published,created_at=now))
        return self.get_result(id)
