"""Admin metadata control only; no scheduler, model or SDK execution in HTTP."""
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin,require_csrf
from api.collector import body,query
from .service import DailyService


def register_daily_routes(app):
    async def guard(request,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin,request)
        if write:await run_in_threadpool(require_csrf,request)
        query(request,set())
        if app.state.task_service is None:raise HTTPException(503,'Daily dependencies unavailable')
        return DailyService(app.state.task_service.repo.engine),actor['id']

    def call(fn,*args):
        try:return fn(*args)
        except PermissionError:raise HTTPException(403,'Current plan owner administrator required') from None
        except LookupError:raise HTTPException(404,'Plan or saved model unavailable') from None
        except ValueError:raise HTTPException(409,'Model, pool or plan boundary changed') from None
        except Exception:raise HTTPException(503,'Daily metadata unavailable; explicit migration required') from None

    @app.get('/api/admin/daily-predictions')
    async def list_plans(request:Request):
        service,_=await guard(request)
        def read():return dict(items=service.repo.list(),runs=service.repo.recent(),automatic_training=False,automatic_publication=False)
        return await run_in_threadpool(call,read)

    @app.post('/api/admin/daily-predictions',status_code=201)
    async def create(request:Request):
        service,actor=await guard(request,True);value=await body(request,{'model_id'})
        return await run_in_threadpool(call,service.create,actor,value['model_id'])

    @app.post('/api/admin/daily-predictions/{id}/enabled')
    async def enabled(id:str,request:Request):
        service,actor=await guard(request,True);value=await body(request,{'enabled'})
        if value['enabled'] not in ('true','false'):raise HTTPException(400,'Explicit enabled flag required')
        return await run_in_threadpool(call,service.enable,actor,id,value['enabled']=='true')
