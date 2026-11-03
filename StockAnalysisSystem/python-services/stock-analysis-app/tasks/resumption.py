"""One stopped-attempt/slot guard for typed manual resume; never requeues/unlocks."""
import json
from sqlalchemy import select
from .repository import tasks,slots
from .contracts import identity,Conflict,normalize_request


def parent_task(connection,id):
    identity(id)
    row=connection.execute(select(tasks).where(tasks.c.id==id)).mappings().one_or_none()
    slot=connection.execute(select(slots).where(slots.c.id==1)).mappings().one()
    if row is None or row['state'] not in ('FAILED','INTERRUPTED','CANCELLED') or slot['quarantined'] or slot['task_id']==id:
        raise Conflict('Resume requires a stopped parent and recovered execution slot')
    request=normalize_request(json.loads(row['request']))
    supported=request['kind']=='feature_build' or request['kind']=='model_workflow' and (
        (request['algorithm'],request['operation']) in (('lightgbm','predict'),('transformer','train')))
    if not supported:raise ValueError('Unsupported resume parent')
    return dict(id=id,request=request,binding=json.loads(row['binding']))


def semantic_identity(task):
    from copy import deepcopy
    from .contracts import digest
    request=normalize_request(task['request']);request.pop('resume_task_id',None)
    binding=deepcopy(task['binding']);binding.pop('resume',None)
    return digest(dict(request=request,binding=binding))
