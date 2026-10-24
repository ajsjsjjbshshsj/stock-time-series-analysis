"""Explicit additive metadata and transactional authorization records."""
import json
from sqlalchemy import MetaData,Table,Column,String,Text,Boolean,BigInteger,select,inspect
from sqlalchemy.dialects.mysql import LONGTEXT
from api.auth.repository import users
from tasks.contracts import digest,identity

metadata=MetaData()
releases=Table('analysis_prediction_model',metadata,
    Column('model_id',String(32),primary_key=True),Column('sponsor_id',String(32),nullable=False),
    Column('revision',String(32),nullable=False),Column('enabled',Boolean,nullable=False),
    Column('proof',Text().with_variant(LONGTEXT(),'mysql'),nullable=False),Column('proof_sha256',String(64),nullable=False),Column('updated_at',BigInteger,nullable=False))
audit=Table('analysis_prediction_model_audit',metadata,
    Column('id',String(32),primary_key=True),Column('actor_id',String(32),nullable=False),Column('model_id',String(32),nullable=False),
    Column('revision',String(32),nullable=False),Column('enabled',Boolean,nullable=False),Column('created_at',BigInteger,nullable=False))
cache=Table('analysis_user_prediction',metadata,
    Column('id',String(64),primary_key=True),Column('model_id',String(32),nullable=False),Column('revision',String(32),nullable=False),
    Column('sponsor_id',String(32),nullable=False),Column('selection',Text().with_variant(LONGTEXT(),'mysql'),nullable=False),Column('binding_sha256',String(64),nullable=False),
    Column('request',Text,nullable=False),Column('task_id',String(32),unique=True),Column('result_id',String(32)),
    Column('created_at',BigInteger,nullable=False))
grants=Table('analysis_user_prediction_access',metadata,
    Column('prediction_id',String(64),primary_key=True),Column('user_id',String(32),primary_key=True),Column('created_at',BigInteger,nullable=False))

def migrate(engine):
    metadata.create_all(engine)
    if engine.dialect.name=='mysql':
        with engine.begin() as c:
            for table,column in ((releases,'proof'),(cache,'selection')):
                info=next(r for r in inspect(c).get_columns(table.name) if r['name']==column)
                if not isinstance(info['type'],LONGTEXT):
                    c.exec_driver_sql(f'ALTER TABLE `{table.name}` MODIFY COLUMN `{column}` LONGTEXT NOT NULL')

def account(c,id,*,admin=False,lock=False):
    identity(id);query=select(users).where(users.c.id==id)
    # Read authorization does not mutate accounts. Locking requester→sponsor
    # here would invert the owned queue's slot→sponsor→requester ordering.
    if lock:query=query.with_for_update()
    row=c.execute(query).mappings().one_or_none()
    if row is None or not row['enabled'] or row['must_change_password'] or admin and row['role']!='admin':
        raise PermissionError('Current authorized account required')
    return row

def release_row(c,id):
    row=c.execute(select(releases).where(releases.c.model_id==id)).mappings().one_or_none()
    if row is None:raise LookupError('Enabled model required')
    value=dict(row);value['proof']=json.loads(value['proof'])
    if digest(value['proof'])!=value['proof_sha256']:raise ValueError('Model release proof changed')
    return value

def check_cache(row,release):
    selection=json.loads(row['selection']);request=json.loads(row['request'])
    codes=selection['stock_codes']
    if (set(selection)!={'pool_version','display_name','stock_codes','stock_count','member_sha256'}
            or not codes or codes!=sorted(set(codes)) or selection['stock_count']!=len(codes)
            or not set(codes)<=set(release['proof']['stock_codes']) or selection['member_sha256']!=digest(codes)):
        raise ValueError('Prediction selection proof changed')
    expected=digest(dict(revision=row['revision'],model_sha256=release['proof']['manifest_sha256'],
        selection=selection,request=request,binding_sha256=row['binding_sha256']))
    if row['id']!=expected:raise ValueError('Prediction cache proof changed')

def execution_guard(c,task):
    # Optional until explicit migration; old administrative jobs are unchanged.
    if not inspect(c).has_table(cache.name):return
    row=c.execute(select(cache).where(cache.c.task_id==task['id'])).mappings().one_or_none()
    if row is None:return
    release=release_row(c,row['model_id']);request=json.loads(task['request']);binding=json.loads(task['binding'])
    check_cache(row,release)
    if (not release['enabled'] or row['revision']!=release['revision'] or row['sponsor_id']!=task['actor_id']
            or digest(binding)!=row['binding_sha256'] or request!=json.loads(row['request'])
            or request.get('kind')!='model_workflow' or request.get('operation')!='predict'
            or request.get('model_id')!=row['model_id']
            or binding.get('model_manifest_sha256')!=release['proof']['manifest_sha256']):
        raise PermissionError('Prediction model release changed')
    current=c.execute(select(users.c.id).join(grants,grants.c.user_id==users.c.id).where(
        grants.c.prediction_id==row['id'],users.c.enabled==True,users.c.must_change_password==False).limit(1)).first()
    if current is None:raise PermissionError('Prediction has no current requester')
