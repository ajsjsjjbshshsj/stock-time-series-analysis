"""Admin-only explicit handoff to the existing owned task queue; no computation."""
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin,require_csrf
from api.collector import query,body,key
from tasks.contracts import identity,Conflict,NotFound


def enqueue(service,actor,parent,request_key):
    try:return service.create(actor,dict(kind='feature_build',preparation_id=parent),request_key)
    except (NotFound,LookupError):raise HTTPException(404,'Preparation not found') from None
    except Conflict:raise HTTPException(409,'Feature request conflict') from None
    except ValueError as error:
        if str(error)=='PREPARATION_NOT_READY':raise HTTPException(409,'Preparation is not ready for a feature build') from None
        raise HTTPException(400,'Invalid feature build binding') from None
    except Exception:raise HTTPException(503,'Feature staging dependencies unavailable') from None


def register_feature_routes(app):
    @app.post('/api/admin/preparation/requests/{parent}/feature-build',status_code=201)
    async def create(parent:str,request:Request):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin,request)
        await run_in_threadpool(require_csrf,request)
        query(request,set());await body(request,set());request_key=key(request)
        try:identity(parent)
        except ValueError:raise HTTPException(400,'Invalid preparation identity') from None
        service=app.state.task_service
        if service is None:raise HTTPException(503,'Feature staging dependencies unavailable')
        return await run_in_threadpool(enqueue,service,actor['id'],parent,request_key)
