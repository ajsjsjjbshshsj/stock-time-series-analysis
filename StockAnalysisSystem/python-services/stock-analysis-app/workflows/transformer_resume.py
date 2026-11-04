"""Torch-free administrative checkpoint binding; native restore lives in the worker."""
from tasks.resumption import parent_task,semantic_identity
from analysis.transformer_checkpoint import EpochCheckpoint


def bind_resume(source,request,binding):
    with source.engine.connect() as connection:parent=parent_task(connection,request['resume_task_id'])
    if (parent['request']['kind']!='model_workflow' or parent['request']['operation']!='train' or parent['request']['algorithm']!='transformer'
            or semantic_identity(dict(request=request,binding=binding))!=semantic_identity(parent)):
        raise ValueError('Transformer resume source/configuration changed')
    try:proof=EpochCheckpoint.inspect(source.store.path(parent['id'])/'training_checkpoints',expected_task=semantic_identity(parent))
    except FileNotFoundError:raise ValueError('No complete Transformer training checkpoint') from None
    return dict(task_id=parent['id'],checkpoint_sha256=proof['fingerprint'],completed_epochs=proof['epoch'])
