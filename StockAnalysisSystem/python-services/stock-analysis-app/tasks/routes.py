"""Protected bounded administrative task API; never imports a trainer/worker."""
import json
import re
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin,require_csrf
from .contracts import Conflict,NotFound,normalize_request,idempotency


def invoke(operation,*args,**kwargs):
    try:return operation(*args,**kwargs)
    except Conflict:raise HTTPException(409,'Task state or idempotency conflict') from None
    except NotFound:raise HTTPException(404,'Task not found') from None
    except PermissionError:raise HTTPException(403,'Access denied') from None
    except Exception:raise HTTPException(503,'Training dependencies not ready') from None


def query(request,keys):
    if any(k not in keys or len(request.query_params.getlist(k))!=1 for k in request.query_params):
        raise HTTPException(400,'Invalid task request')


def number(value,minimum=1,maximum=100):
    if not re.fullmatch('[0-9]{1,19}',value) or not minimum<=int(value)<=maximum:
        raise HTTPException(400,'Invalid task request')
    return int(value)


def register_task_routes(app):
    async def guard(request,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin,request)
        if write:await run_in_threadpool(require_csrf,request)
        service=app.state.task_service
        if service is None:raise HTTPException(503,'Training dependencies not ready')
        return service,actor

    @app.get('/api/admin/training/capabilities')
    async def capabilities(request:Request):
        service,_=await guard(request);query(request,set())
        source=await run_in_threadpool(invoke,service.source.describe)
        slot=await run_in_threadpool(invoke,service.slot_status)
        return dict(kinds=[dict(kind='xgboost_single',enabled=True),dict(kind='lightgbm_ranking',enabled=False),
            dict(kind='transformer',enabled=False)],purpose='engineering_smoke',source=source,
            limits=dict(n_estimators=100,max_depth=6,concurrency=1),
            execution=dict(task_id=slot['task_id'],quarantined=slot['quarantined']))

    @app.get('/api/admin/training/jobs')
    async def list_jobs(request:Request,limit:str='20',before:str|None=None):
        service,_=await guard(request);query(request,{'limit','before'})
        return await run_in_threadpool(invoke,service.list,limit=number(limit),
            before=number(before,1,2**63-1) if before is not None else None)

    @app.post('/api/admin/training/jobs',status_code=201)
    async def create_job(request:Request):
        service,actor=await guard(request,True);query(request,set())
        try:
            keys=request.headers.getlist('idempotency-key')
            if len(keys)!=1:raise ValueError('Idempotency key required')
            key=idempotency(keys[0]);raw=bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw)>16384:raise ValueError('Request too large')
            def unique(pairs):
                result={}
                for k,v in pairs:
                    if k in result:raise ValueError('Duplicate field')
                    result[k]=v
                return result
            body=json.loads(raw,object_pairs_hook=unique)
            value=normalize_request(body)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid task request') from None
        return await run_in_threadpool(invoke,service.create,actor['id'],value,key)

    @app.get('/api/admin/training/jobs/{task_id}')
    async def get_job(task_id:str,request:Request):
        service,_=await guard(request);query(request,set())
        if not re.fullmatch('[0-9a-f]{32}',task_id):raise HTTPException(400,'Invalid task request')
        return await run_in_threadpool(invoke,service.get,task_id)

    @app.get('/api/admin/training/jobs/{task_id}/events')
    async def job_events(task_id:str,request:Request,after:str='0',limit:str='100'):
        service,_=await guard(request);query(request,{'after','limit'})
        if not re.fullmatch('[0-9a-f]{32}',task_id):raise HTTPException(400,'Invalid task request')
        return await run_in_threadpool(invoke,service.events,task_id,after=number(after,0,2**63-1),limit=number(limit))

    @app.post('/api/admin/training/jobs/{task_id}/cancel')
    async def cancel_job(task_id:str,request:Request):
        service,actor=await guard(request,True);query(request,set())
        if not re.fullmatch('[0-9a-f]{32}',task_id):raise HTTPException(400,'Invalid task request')
        return await run_in_threadpool(invoke,service.cancel,actor['id'],task_id)
