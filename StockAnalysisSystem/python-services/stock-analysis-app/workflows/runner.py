"""Owned-child entrypoint; all heavy operations produce a new immutable version."""
import time
from tasks.artifacts import task_digest,read_json
from tasks.market_profile import bounded_profile
from tasks import resources
from .artifacts import ArtifactStore


def run_operation(task,root,source,emit):
    store=ArtifactStore(root);folder=store.path(task['id']);resources.preflight(folder,bounded_profile())
    emit('stage',stage='PREFLIGHT');source.verify(task);folder.mkdir(parents=True,exist_ok=False)
    request=task['request'];operation=request['operation']
    if operation=='train':
        from .training import train_operation
        info=train_operation(task,folder,source,emit);kind='model';model_id=task['id']
    elif operation=='import':
        from .imports import import_operation
        info=import_operation(task,folder,source,emit);kind='model';model_id=task['id']
    elif operation=='predict':
        from .inference import predict_operation
        info=predict_operation(task,folder,source,emit);kind='result';model_id=request['model_id']
    else:
        from .backtest import backtest_operation
        info=backtest_operation(task,folder,source,emit);kind='result';model_id=request['model_id']
    emit('stage',stage='VERIFY');source.verify(task)
    native=read_json(folder/'native.json',max_bytes=8*1024*1024) if kind=='model' else None
    info=dict(info,status=info.get('status','COMPLETE'),operation=operation)
    index=dict(artifact_kind=kind,algorithm=request['algorithm'],model_id=model_id,created_at=int(time.time()),
        title=f"{request['algorithm']} · {info.get('ts_code') or operation} · {request['end_date'] or request['import_task_id'] or request['experiment_id']}",metadata=info)
    proof=store.seal(task['id'],dict(schema_version=1,task_sha256=task_digest(task),index=index,native=native,
        request=request,binding=task['binding'],published=False))
    emit('stage',stage='SAVE');return proof
