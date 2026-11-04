"""Terminal-attempt reads and slot checks; never requeue or clear quarantine."""
import json
from sqlalchemy import select
from tasks.repository import tasks,slots
from tasks.contracts import identity,Conflict
from .contracts import normalize_operation
from .prediction_checkpoint import Checkpoint,semantic_identity


def parent_task(connection,id):
    identity(id)
    row=connection.execute(select(tasks).where(tasks.c.id==id)).mappings().one_or_none()
    slot=connection.execute(select(slots).where(slots.c.id==1)).mappings().one()
    if (row is None or row['state'] not in ('FAILED','INTERRUPTED','CANCELLED')
            or slot['quarantined'] or slot['task_id']==id):
        raise Conflict('Resume requires a stopped terminal parent and a recovered execution slot')
    request=normalize_operation(json.loads(row['request']))
    if request['operation']!='predict' or request['algorithm']!='lightgbm':raise ValueError('Unsupported resume parent')
    return dict(id=id,request=request,binding=json.loads(row['binding']))


def bind_resume(source,request,binding):
    with source.engine.connect() as connection:parent=parent_task(connection,request['resume_task_id'])
    current=dict(id='0'*32,request=request,binding=binding)
    if semantic_identity(current)!=semantic_identity(parent):raise ValueError('Resume source/model/request changed')
    proof=Checkpoint.inspect(parent,source.store.path(parent['id'])/'prediction_checkpoint')
    if not proof['completed_codes']:raise ValueError('No committed prediction checkpoint')
    return dict(task_id=parent['id'],checkpoint_sha256=proof['fingerprint'],completed_stocks=len(proof['completed_codes']))
