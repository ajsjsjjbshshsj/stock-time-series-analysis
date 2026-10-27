"""Protected collection control plane. No SDK, Runtime, shell or HTTP acquisition."""
from datetime import datetime, timedelta, time
from pathlib import Path
import json
import os
import re
import sys
import socket

from fastapi import Request, HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin, require_csrf


def collector_imports():
    # Sibling service is a trusted source dependency, not a caller-selected path.
    root = Path(__file__).resolve().parents[2]/'python-collector'
    if str(root) not in sys.path: sys.path.append(str(root))
    from app.repositories.web_collection_repository import CollectionCommands
    return CollectionCommands


def production_repository(engine):
    from sqlalchemy.orm import sessionmaker
    return collector_imports()(sessionmaker(bind=engine))


def invoke(operation, *args, **kwargs):
    try: return operation(*args, **kwargs)
    except LookupError: raise HTTPException(404, 'Collection item not found') from None
    except ValueError as error:
        if str(error) in {'IDEMPOTENCY_CONFLICT', 'TASK_NOT_RETRYABLE', 'RETRY_DATE_CHANGED'}:
            raise HTTPException(409, 'Collection request conflict') from None
        raise HTTPException(400, 'Invalid collection request') from None
    except Exception: raise HTTPException(503, 'Collection dependencies unavailable; check explicit migration and worker') from None


def query(request, allowed):
    if any(k not in allowed or len(request.query_params.getlist(k)) != 1 for k in request.query_params):
        raise HTTPException(400, 'Invalid collection query')


def number(raw, low, high):
    if not re.fullmatch('[0-9]{1,6}', raw) or not low <= int(raw) <= high:
        raise HTTPException(400, 'Invalid collection pagination')
    return int(raw)


def public_command(value):
    return {k: value[k] for k in ('id', 'start_date', 'end_date', 'target_task', 'status',
        'batch_id', 'error_kind', 'retry_not_before', 'created_at', 'updated_at', 'task_count')}


def runtime_status():
    """Read-only probes; absent/uncertain ownership is never presented as running."""
    collector_imports()
    from app.runtime_paths import RuntimePaths
    from app.preparation.contracts import TZ
    from app.nightly.scheduler import NightlyScheduler
    project = Path(__file__).resolve().parents[3]
    paths = RuntimePaths.from_environment(project, os.environ)
    root = paths.data_root
    state = 'STOPPED'
    lock = root/'worker.lock'
    if lock.exists():
        try:
            if not lock.resolve().is_relative_to(root): raise ValueError('OUTSIDE_ROOT')
            owner = json.loads(lock.read_text(encoding='utf-8'))
            # psutil is already an analysis-service dependency. Unlike the CLI
            # ownership probe this never launches PowerShell in an HTTP handler.
            import psutil
            if owner.get('host') != socket.gethostname() or type(owner.get('pid')) is not int or owner['pid'] <= 0:
                state = 'UNKNOWN'
            else:
                try:
                    started = datetime.fromisoformat(owner['started_at'])
                    if started.tzinfo is None: raise ValueError('Naive owner timestamp')
                    actual = psutil.Process(owner['pid']).create_time()
                    state = 'RUNNING' if abs(started.timestamp()-actual) < .001 else 'STALE_LOCK'
                except psutil.NoSuchProcess: state = 'STALE_LOCK'
        except Exception: state = 'UNKNOWN'
    scheduler = NightlyScheduler(None, None, root, run_at=os.getenv('NIGHTLY_UPDATE_AT', '20:00'))
    status = scheduler.status()
    now = datetime.now(TZ)
    cutoff = now.date() if now.time() > time(16) else now.date()-timedelta(days=1)
    return dict(scope='SH_SZ_A', source='tushare', verification_mode='missing-only',
        start_min='2023-01-01', completed_date=cutoff.isoformat(), worker=state,
        scheduler={k: status[k] for k in ('run_at', 'start_date', 'blocked_error', 'retry_not_before', 'last_scan_at')},
        note='Worker liveness does not prove nightly-enabled arguments; start the documented --nightly worker.')


async def body(request, fields):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 4096: raise HTTPException(400, 'Collection body too large')
    try:
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result: raise ValueError('Duplicate field')
                result[key] = value
            return result
        result = json.loads(raw, object_pairs_hook=unique)
        if type(result) is not dict or set(result) != fields: raise ValueError()
        if any(type(v) is not str for v in result.values()): raise ValueError()
        return result
    except (ValueError, TypeError, UnicodeError): raise HTTPException(400, 'Invalid collection body') from None


def key(request):
    values = request.headers.getlist('idempotency-key')
    if len(values) != 1 or not re.fullmatch('[0-9a-f]{32}', values[0]):
        raise HTTPException(400, 'Collection idempotency key required')
    return values[0]


def register_collector_routes(app):
    async def guard(request, write=False):
        if app.state.auth_settings.mode != 'protected': raise HTTPException(503, 'Protected mode required')
        actor = await run_in_threadpool(require_admin, request)
        if write: await run_in_threadpool(require_csrf, request)
        repo = getattr(app.state, 'collection_repository', None)
        if repo is None:
            repo = await run_in_threadpool(invoke, production_repository, app.state.auth_service.repo.engine)
        return repo, actor

    @app.get('/api/admin/collector/capabilities')
    async def capabilities(request: Request):
        repo, _ = await guard(request); query(request, set())
        await run_in_threadpool(invoke, repo.summary)  # Missing tables must not look ready.
        return await run_in_threadpool(invoke, runtime_status)

    @app.get('/api/admin/collector/summary')
    async def summary(request: Request):
        repo, _ = await guard(request); query(request, set())
        return await run_in_threadpool(invoke, repo.summary)

    @app.get('/api/admin/collector/requests')
    async def requests(request: Request, limit: str = '20', offset: str = '0'):
        repo, _ = await guard(request); query(request, {'limit', 'offset'})
        return await run_in_threadpool(invoke, repo.list_commands, number(limit, 1, 50), number(offset, 0, 100000))

    @app.post('/api/admin/collector/requests', status_code=202)
    async def submit(request: Request):
        repo, actor = await guard(request, True); query(request, set())
        request_key = key(request); value = await body(request, {'start', 'end'})
        result = await run_in_threadpool(invoke, repo.submit, actor['id'], request_key, value['start'], value['end'])
        return public_command(result)

    @app.get('/api/admin/collector/tasks')
    async def tasks(request: Request, limit: str = '20', offset: str = '0', request_id: str | None = None):
        repo, _ = await guard(request); query(request, {'limit', 'offset', 'request_id'})
        return await run_in_threadpool(invoke, repo.list_tasks, number(limit, 1, 50), number(offset, 0, 100000), request_id)

    @app.get('/api/admin/collector/tasks/{id}')
    async def task(id: str, request: Request):
        repo, _ = await guard(request); query(request, set())
        return await run_in_threadpool(invoke, repo.task, id)

    @app.post('/api/admin/collector/tasks/{id}/retry', status_code=202)
    async def retry(id: str, request: Request):
        repo, actor = await guard(request, True); query(request, set())
        request_key = key(request); await body(request, set())
        task = await run_in_threadpool(invoke, repo.task, id)
        result = await run_in_threadpool(invoke, repo.submit, actor['id'], request_key,
            task['trade_date'], task['trade_date'], target=id)
        return public_command(result)

    @app.get('/api/admin/collector/preview/{table}')
    async def preview(table: str, request: Request, limit: str = '20'):
        repo, _ = await guard(request); query(request, {'limit'})
        return await run_in_threadpool(invoke, repo.preview, table, number(limit, 1, 50))
