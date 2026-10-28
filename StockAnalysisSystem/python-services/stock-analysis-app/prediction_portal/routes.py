"""User saved-model inference permission is separate from admin training permission."""
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_user,require_admin,require_csrf
from workflows.routes import body,opaque
from tasks.routes import query,number
from tasks.contracts import Conflict
from .service import PredictionService


def register_prediction_routes(app):
    async def guard(request,*,admin=False,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin if admin else require_user,request)
        if write:await run_in_threadpool(require_csrf,request)
        if app.state.task_service is None:raise HTTPException(503,'Prediction dependencies unavailable')
        service=getattr(app.state,'prediction_service',None) or PredictionService(app.state.task_service)
        return service,actor['id']

    def call(fn,*args,**kwargs):
        try:return fn(*args,**kwargs)
        except LookupError:raise HTTPException(404,'Prediction or enabled model unavailable') from None
        except PermissionError:raise HTTPException(403,'Access denied or model approval revoked') from None
        except Conflict:raise HTTPException(409,'Prediction pending, queue limited or inputs changed') from None
        except ValueError:raise HTTPException(400,'Invalid or incompatible prediction selection') from None
        except Exception:raise HTTPException(503,'Prediction dependencies unavailable; check explicit migration, seals and data') from None

    @app.get('/api/analysis/user-predictions/catalog')
    async def catalog(request:Request):
        s,a=await guard(request);query(request,set());return await run_in_threadpool(call,s.catalog,a)

    @app.post('/api/analysis/user-predictions',status_code=202)
    async def submit(request:Request):
        s,a=await guard(request,write=True);query(request,set());value=await body(request)
        return await run_in_threadpool(call,s.submit,a,value)

    @app.get('/api/analysis/user-predictions/{id}')
    async def status(id:str,request:Request):
        s,a=await guard(request);query(request,set());return await run_in_threadpool(call,s.get,a,id)

    @app.get('/api/analysis/user-predictions/{id}/scores')
    async def scores(id:str,request:Request,limit:str='50',after:str='0'):
        s,a=await guard(request);query(request,{'limit','after'})
        return await run_in_threadpool(call,s.result,a,id,limit=number(limit,1,100),after=number(after,0,10000000))

    @app.get('/api/admin/prediction-models')
    async def models(request:Request):
        s,a=await guard(request,admin=True);query(request,set());return await run_in_threadpool(call,s.admin_models,a)

    @app.get('/api/analysis/user-predictions/{id}/coverage')
    async def coverage(id:str,request:Request,limit:str='20',after:str='0'):
        s,a=await guard(request);query(request,{'limit','after'})
        return await run_in_threadpool(call,s.coverage,a,id,limit=number(limit,1,100),after=number(after,0,10000))

    @app.post('/api/admin/prediction-models/{id}')
    async def release(id:str,request:Request):
        s,a=await guard(request,admin=True,write=True);query(request,set());value=await body(request)
        if set(value)!={'enabled'} or type(value['enabled']) is not bool:raise HTTPException(400,'Explicit model approval required')
        return await run_in_threadpool(call,s.release,a,opaque(id),value['enabled'])
