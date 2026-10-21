"""Protected administrative workflows and login-required published-only reads."""
import json,re
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_user,require_admin,require_csrf
from tasks.contracts import NotFound,Conflict,idempotency
from tasks.routes import query,number
from .contracts import normalize_operation
from .service import SECTIONS


def invoke(operation,*args,**kwargs):
    try:return operation(*args,**kwargs)
    except NotFound:raise HTTPException(404,'Workflow resource not found') from None
    except Conflict:raise HTTPException(409,'Workflow state conflict') from None
    except PermissionError:raise HTTPException(403,'Access denied') from None
    except ValueError:raise HTTPException(400,'Invalid workflow request') from None
    except Exception:raise HTTPException(503,'Workflow dependencies unavailable') from None


def opaque(value):
    if not re.fullmatch('[0-9a-f]{32}',value):raise HTTPException(400,'Invalid workflow identity')
    return value


async def body(request,*,empty=False):
    raw=bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw)>16384:raise HTTPException(400,'Workflow request too large')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('Duplicate field')
            result[k]=v
        return result
    try:
        value=json.loads(raw,object_pairs_hook=unique) if raw else {} if empty else None
        if not isinstance(value,dict):raise ValueError('Object required')
        return value
    except (ValueError,TypeError):raise HTTPException(400,'Invalid workflow request') from None


def register_workflow_routes(app):
    async def guard(request,*,admin=False,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin if admin else require_user,request)
        if write:await run_in_threadpool(require_csrf,request)
        service=app.state.workflow_service
        if service is None:raise HTTPException(503,'Workflow dependencies unavailable')
        return service,actor

    @app.get('/api/admin/workflows/capabilities')
    async def capabilities(request:Request):
        s,_=await guard(request,admin=True);query(request,set())
        return await run_in_threadpool(invoke,s.capabilities)

    @app.get('/api/admin/workflows/jobs')
    async def jobs(request:Request,limit:str='20',before:str|None=None):
        s,_=await guard(request,admin=True);query(request,{'limit','before'})
        return await run_in_threadpool(invoke,s.jobs,limit=number(limit,1,20),before=number(before,1,2**63-1) if before else None)

    @app.post('/api/admin/workflows/jobs',status_code=201)
    async def create(request:Request):
        s,actor=await guard(request,admin=True,write=True);query(request,set())
        value=await body(request)
        try:
            keys=request.headers.getlist('idempotency-key')
            if len(keys)!=1:raise ValueError('Idempotency key required')
            key=idempotency(keys[0]);value=normalize_operation(value)
        except (ValueError,TypeError):raise HTTPException(400,'Invalid workflow request') from None
        return await run_in_threadpool(invoke,s.create,actor['id'],value,key)

    @app.get('/api/admin/workflows/models')
    async def models(request:Request,limit:str='20',before:str|None=None):
        s,_=await guard(request,admin=True);query(request,{'limit','before'})
        return await run_in_threadpool(invoke,s.models,limit=number(limit,1,20),before=number(before,1,2**63-1) if before else None)

    async def listing(request,limit,before,public):
        s,_=await guard(request,admin=not public);query(request,{'limit','before'})
        return await run_in_threadpool(invoke,s.results,public=public,limit=number(limit,1,20),before=number(before,1,2**63-1) if before else None)

    @app.get('/api/admin/workflows/results')
    async def admin_results(request:Request,limit:str='20',before:str|None=None):return await listing(request,limit,before,False)

    @app.get('/api/analysis/published-results')
    async def published_results(request:Request,limit:str='20',before:str|None=None):return await listing(request,limit,before,True)

    async def detail(request,id,section,limit,after,ts_code,public):
        s,_=await guard(request,admin=not public);query(request,{'section','limit','after','ts_code'})
        if section not in SECTIONS:raise HTTPException(400,'Invalid workflow section')
        if ts_code is not None and not re.fullmatch(r'[0-9]{6}\.(SH|SZ)',ts_code):raise HTTPException(400,'Invalid stock')
        return await run_in_threadpool(invoke,s.result,opaque(id),public=public,section=section,limit=number(limit),
            after=number(after,0,10000000),ts_code=ts_code)

    @app.get('/api/admin/workflows/results/{id}')
    async def admin_result(id:str,request:Request,section:str='scores',limit:str='50',after:str='0',ts_code:str|None=None):
        return await detail(request,id,section,limit,after,ts_code,False)

    @app.get('/api/analysis/published-results/{id}')
    async def published_result(id:str,request:Request,section:str='scores',limit:str='50',after:str='0',ts_code:str|None=None):
        return await detail(request,id,section,limit,after,ts_code,True)

    async def visibility(request,id,published):
        s,actor=await guard(request,admin=True,write=True);query(request,set())
        if await body(request,empty=True):raise HTTPException(400,'Unexpected publication fields')
        return await run_in_threadpool(invoke,s.publication,actor['id'],opaque(id),published)

    @app.post('/api/admin/workflows/results/{id}/publish')
    async def publish(id:str,request:Request):return await visibility(request,id,True)

    @app.post('/api/admin/workflows/results/{id}/withdraw')
    async def withdraw(id:str,request:Request):return await visibility(request,id,False)
