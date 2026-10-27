"""Operational admission, separate from immutable model and resource profiles."""
from sqlalchemy import select
from tasks.contracts import identity

RETRY_SECONDS=60
MAX_PREFLIGHT_RETRIES=2


def check_resources():
    from tasks.resources import preflight
    from tasks.market_profile import bounded_profile
    from workflows.source import artifact_root
    return preflight(artifact_root(),bounded_profile())


def retryable_preflight(engine,job):
    """Never retry interrupted work, partial outputs or a publication intent."""
    if (job['state']!='FAILED' or job.get('failure_code')!='RESOURCE_UNAVAILABLE'
            or job.get('candidate') is not None):return False
    from tasks.artifacts import _is_link
    from workflows.source import artifact_root
    from .repository import activations
    identity(job['id'])
    folder=artifact_root()/job['id']
    if _is_link(folder) or folder.exists():return False
    with engine.connect() as connection:
        return connection.execute(select(activations.c.task_id).where(activations.c.task_id==job['id'])).first() is None


def retry_state(engine,state,field,job,now):
    if not retryable_preflight(engine,job):return None
    history={key:list(ids) for key,ids in state.get('resource_retries',{}).items()}
    attempts=history.get(field,[])
    if len(attempts)>=MAX_PREFLIGHT_RETRIES:
        return dict(state,status='BLOCKED',reason='RESOURCE_RETRIES_EXHAUSTED')
    history[field]=[*attempts,job['id']]
    new={key:value for key,value in state.items() if key!=field}
    return dict(new,resource_retries=history,status='WAITING_DEPENDENCY',
                reason='RESOURCE_UNAVAILABLE',retry_at=now+RETRY_SECONDS)


def attempt_key(key,state,field):
    attempts=len(state.get('resource_retries',{}).get(field,[]))
    # Attempt zero must keep existing persistent identities, including lost ACKs.
    return dict(key,resource_attempt=attempts) if attempts else key
