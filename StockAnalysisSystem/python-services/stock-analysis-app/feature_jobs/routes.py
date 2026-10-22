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
    async def guard(request,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin,request)
        if write:await run_in_threadpool(require_csrf,request)
        if app.state.task_service is None:raise HTTPException(503,'Feature dependencies unavailable')
        return actor

    @app.get('/api/admin/features')
    async def builds(request:Request):
        await guard(request);query(request,set())
        def read():
            import json
            from sqlalchemy import select,text
            from tasks.repository import tasks
            engine=app.state.task_service.repo.engine
            with engine.connect() as c:
                active=c.execute(text("SELECT build_id,source_start,source_cutoff FROM stock_feature_build WHERE active_slot='traditional_v2' AND state='READY'")).mappings().one_or_none()
                rows=c.execute(select(tasks.c.id,tasks.c.candidate).where(tasks.c.state=='SUCCEEDED',tasks.c.request.like('%"kind":"feature_build"%')).order_by(tasks.c.seq.desc()).limit(50)).mappings().all()
            return dict(active=dict(active) if active else None,items=[dict(task_id=r['id'],candidate=json.loads(r['candidate'])) for r in rows])
        try:return await run_in_threadpool(read)
        except Exception:raise HTTPException(503,'Feature metadata unavailable') from None

    async def activate(parent,request,action):
        actor=await guard(request,True);query(request,set());await body(request,set());request_key=key(request)
        try:
            identity(parent)
            return await run_in_threadpool(app.state.task_service.create,actor['id'],
                dict(kind='feature_activate',feature_task_id=parent,action=action),request_key)
        except (NotFound,LookupError):raise HTTPException(404,'Feature build unavailable') from None
        except Conflict:raise HTTPException(409,'Feature activation conflict') from None
        except ValueError:raise HTTPException(409,'Feature seal, active version or recovery state changed') from None
        except Exception:raise HTTPException(503,'Feature activation dependencies unavailable') from None

    @app.post('/api/admin/features/{parent}/activate',status_code=201)
    async def enable(parent:str,request:Request):return await activate(parent,request,'activate')

    @app.post('/api/admin/features/{parent}/rollback',status_code=201)
    async def rollback(parent:str,request:Request):return await activate(parent,request,'rollback')

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
