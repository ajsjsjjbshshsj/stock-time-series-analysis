"""Managed feature publication contract; execution uses the original owned slot."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field


class ActivationRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['feature_activate']
    feature_task_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    action:Literal['activate','rollback']='activate'


def normalize(value):return ActivationRequest.model_validate(value).model_dump()


def original_task(engine,id):
    import json
    from sqlalchemy import select
    from tasks.repository import tasks
    with engine.connect() as c:row=c.execute(select(tasks).where(tasks.c.id==id)).mappings().one_or_none()
    if row is None:raise LookupError('Feature task not found')
    request=json.loads(row['request'])
    if row['state']!='SUCCEEDED' or request['kind']!='feature_build' or not row['candidate']:
        raise ValueError('Completed feature build required')
    return dict(id=row['id'],request=request,binding=json.loads(row['binding'])),json.loads(row['candidate'])


class ActivationSource:
    def __init__(self,engine):self.engine=engine

    def bind(self,value):
        from sqlalchemy import text
        from tasks.contracts import digest
        from tasks.source import APP
        from workflows.source import artifact_root
        from .runner import verify_build
        from automation.repository import ActivationJournal
        request=normalize(value);old,stored=original_task(self.engine,request['feature_task_id'])
        proof=verify_build(old,artifact_root(),self.engine,hash_files=False,states=('BUILDING','READY'),require_inactive=False)
        if proof!=stored:raise ValueError('Feature candidate and seal differ')
        with self.engine.connect() as c:
            active=c.execute(text("SELECT build_id,state,source_cutoff FROM stock_feature_build WHERE active_slot='traditional_v2'")).mappings().one_or_none()
            target=c.execute(text('SELECT state,active_slot FROM stock_feature_build WHERE build_id=:id'),dict(id=old['id'])).mappings().one()
        previous=active['build_id'] if active else None
        pending=ActivationJournal(self.engine).pending()
        if pending:
            if any(p['build_id']!=old['id'] or p['action']!=request['action'] for p in pending):raise ValueError('Resolve original pending publication first')
            expected={p['previous_id'] for p in pending}
            if len(expected)!=1:raise ValueError('Conflicting publication recovery intents')
            previous=expected.pop()
        elif request['action']=='activate' and previous!=old['id'] and previous!=old['binding']['previous_active_build_id']:
            raise ValueError('Feature build previous active version changed')
        if request['action']=='rollback' and target['state']!='READY':raise ValueError('Rollback requires an archived verified build')
        if active and request['action']=='activate' and proof['source_cutoff']<str(active['source_cutoff']):
            raise ValueError('Activation cannot shorten active history')
        files=('database/feature_store_publication.py','feature_jobs/activation.py','feature_jobs/publication_control.py')
        return dict(build_id=old['id'],feature_manifest_sha256=proof['manifest_sha256'],
            previous_active_build_id=previous,seen_active_build_id=active['build_id'] if active else None,
            recovery_ids=sorted(p['task_id'] for p in pending),source_start=proof['source_start'],source_cutoff=proof['source_cutoff'],
            source_sha256=proof['source_sha256'],output_sha256=proof['output_sha256'],
            code_sha256=digest({name:(APP/name).read_text(encoding='utf-8') for name in files}))

    def verify(self,task):
        from tasks.source import SourceChanged
        if self.bind(task['request'])!=task['binding']:raise SourceChanged('Activation binding changed')


def validate_summary(value,task):
    import re
    fields={'task_id','purpose','verified','manifest_sha256','build_id','previous_build_id','action','activated','model_ready'}
    if (not isinstance(value,dict) or set(value)!=fields or value['task_id']!=task['id']
            or value['purpose']!='feature_activate' or value['verified'] is not True
            or value['build_id']!=task['binding']['build_id'] or value['previous_build_id']!=task['binding']['previous_active_build_id']
            or value['action']!=task['request']['action'] or value['activated'] is not True or value['model_ready'] is not False
            or not isinstance(value['manifest_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['manifest_sha256']) is None):
        raise ValueError('Invalid activation proof')
    return value


def verify_activation(task,root,engine):
    from sqlalchemy import text
    from tasks.artifacts import task_path,task_digest,read_json,file_digest,_is_link
    from automation.repository import ActivationJournal,intent
    folder=task_path(root,task['id'])
    if {p.name for p in folder.iterdir()}!={'activation.json','complete.json'} or any(_is_link(p) for p in folder.iterdir()):
        raise ValueError('Invalid activation evidence files')
    value=read_json(folder/'activation.json');sha=file_digest(folder/'activation.json')
    if (value['task_sha256']!=task_digest(task) or value['binding']!=task['binding']
            or value['receipt'].get('build_id')!=task['binding']['build_id'] or value['receipt'].get('published') is not True
            or read_json(folder/'complete.json')!=dict(manifest_sha256=sha)):
        raise ValueError('Activation evidence changed')
    row=ActivationJournal(engine).get(task['id'])
    expected=dict(task_id=task['id'],build_id=task['binding']['build_id'],previous_id=task['binding']['previous_active_build_id'],action=task['request']['action'])
    if intent(row)!=expected or row['state']!='COMMITTED':raise ValueError('Activation is not committed')
    with engine.connect() as c:
        active=c.execute(text("SELECT build_id,state FROM stock_feature_build WHERE active_slot='traditional_v2'")).mappings().one_or_none()
    if active is None or active['build_id']!=expected['build_id'] or active['state']!='READY':raise ValueError('Active features differ from committed task')
    return validate_summary(dict(task_id=task['id'],purpose='feature_activate',verified=True,manifest_sha256=sha,
        build_id=expected['build_id'],previous_build_id=expected['previous_id'],action=expected['action'],activated=True,model_ready=False),task)


def run_activation(task,root,db,emit,owner_guard):
    from tasks.artifacts import task_path,task_digest,write_json,file_digest
    from tasks import resources
    from tasks.market_profile import bounded_profile
    from workflows.source import artifact_root
    from .runner import verify_build
    from .publication_control import ManagedPublication
    from database.feature_store_publication import publish
    ActivationSource(db.engine).verify(task);owner_guard()
    folder=task_path(root,task['id']);resources.preflight(folder,bounded_profile());folder.mkdir(parents=True,exist_ok=False)
    old,_=original_task(db.engine,task['request']['feature_task_id'])
    emit('stage',stage='INPUT')
    verify_build(old,artifact_root(),db.engine,states=('BUILDING','READY'),require_inactive=False)
    emit('stage',stage='VERIFY')
    managed=ManagedPublication(db.engine,dict(task_id=task['id'],build_id=task['binding']['build_id'],
        previous_id=task['binding']['previous_active_build_id'],action=task['request']['action']),owner_guard,
        recovery_ids=task['binding']['recovery_ids'])
    receipt=publish(db,task['binding']['build_id'],rollback=task['request']['action']=='rollback',managed=managed)
    write_json(folder/'activation.json',dict(task_sha256=task_digest(task),binding=task['binding'],receipt=receipt))
    write_json(folder/'complete.json',dict(manifest_sha256=file_digest(folder/'activation.json')))
    proof=verify_activation(task,root,db.engine);emit('stage',stage='SAVE');return proof
