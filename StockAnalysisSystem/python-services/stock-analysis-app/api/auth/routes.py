"""Same-origin session and admin routes, with opt-in protected API boundary."""
from fastapi import HTTPException,Request,Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel,ConfigDict,Field
from starlette.concurrency import run_in_threadpool
from .dependencies import service,token,client_ip,require_user,require_admin,require_csrf
from .service import RateLimited


class StrictBody(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)


class Login(StrictBody):
    username: str=Field(min_length=3,max_length=64)
    password: str=Field(min_length=1,max_length=128)


class NewUser(Login):
    role: str


class ChangePassword(StrictBody):
    old_password: str=Field(max_length=128)
    new_password: str=Field(min_length=12,max_length=128)


class UpdateUser(StrictBody):
    role: str|None=None
    enabled: bool|None=None
    new_password: str|None=Field(default=None,min_length=12,max_length=128)


def invoke(operation,*args,**kwargs):
    try: return operation(*args,**kwargs)
    except RateLimited: raise HTTPException(429,'Too many attempts') from None
    except PermissionError: raise HTTPException(403,'Access denied') from None
    except ValueError: raise HTTPException(400,'Invalid request') from None
    except Exception: raise HTTPException(503,'Authentication dependency unavailable') from None


def register_auth_routes(app):
    @app.middleware('http')
    async def authentication_boundary(request,call_next):
        path=request.url.path
        if app.state.auth_settings.mode=='protected' and (path.startswith('/api/') and not path.startswith('/api/auth/')):
            try:
                await run_in_threadpool(require_admin if path.startswith('/api/admin/') else require_user,request)
            except HTTPException as error:
                return JSONResponse({'detail':error.detail},status_code=error.status_code,headers={'Cache-Control':'no-store'})
        response=await call_next(request)
        if path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
            response.headers['X-Content-Type-Options']='nosniff'
        return response

    def envelope(request,response,result,anonymous=False):
        cfg=app.state.auth_settings
        response.set_cookie(cfg.cookie,result['token'],max_age=900 if anonymous else 28800,
            secure=cfg.secure,httponly=True,samesite='lax',path='/')
        return dict(mode='protected',authenticated=result['account'] is not None,
                    user=result['account'],csrf=result['csrf'])

    @app.get('/api/auth/session')
    def session_context(request:Request,response:Response):
        if request.query_params: raise HTTPException(400,'Invalid request')
        if app.state.auth_settings.mode=='local':
            return dict(mode='local',authenticated=False,user=None,csrf=None)
        result=invoke(service(request).context,token(request),client_ip(request))
        return envelope(request,response,result,anonymous=result['account'] is None)

    @app.post('/api/auth/login')
    def login(body:Login,request:Request,response:Response):
        require_csrf(request)
        try:
            result=service(request).login(body.username,body.password,client_ip(request),previous_token=token(request))
        except RateLimited: raise HTTPException(429,'Too many attempts') from None
        except (PermissionError,ValueError): raise HTTPException(401,'Invalid credentials') from None
        except Exception: raise HTTPException(503,'Authentication dependency unavailable') from None
        return envelope(request,response,result)

    @app.post('/api/auth/logout')
    def logout(request:Request,response:Response):
        require_csrf(request)
        invoke(service(request).logout,token(request))
        response.delete_cookie(app.state.auth_settings.cookie,path='/',secure=app.state.auth_settings.secure,
                               httponly=True,samesite='lax')
        return dict(ok=True)

    @app.post('/api/auth/password')
    def change_password(body:ChangePassword,request:Request,response:Response):
        require_csrf(request); require_user(request,allow_password_change=True)
        invoke(service(request).change_password,token(request),body.old_password,body.new_password)
        response.delete_cookie(app.state.auth_settings.cookie,path='/',secure=app.state.auth_settings.secure,
                               httponly=True,samesite='lax')
        return dict(ok=True)

    @app.get('/api/auth/check',status_code=204)
    def check(request:Request):
        if app.state.auth_settings.mode!='protected': raise HTTPException(503,'Protected mode required')
        require_user(request)
        return Response(status_code=204)

    @app.get('/api/auth/check-admin',status_code=204)
    def check_admin(request:Request):
        if app.state.auth_settings.mode!='protected': raise HTTPException(503,'Protected mode required')
        require_admin(request)
        return Response(status_code=204)

    @app.get('/api/auth/readiness',status_code=204)
    def readiness(request:Request):
        # Internal gateway startup probe, never an authentication bypass.
        if app.state.auth_settings.mode!='protected': raise HTTPException(503,'Protected mode required')
        def probe():
            from sqlalchemy import select
            from .repository import schema
            with service(request).repo.transaction() as c:
                for table in schema.sorted_tables: c.execute(select(table).limit(0))
        invoke(probe)
        return Response(status_code=204,headers={'X-Auth-Mode':'protected','X-Auth-Origin':app.state.auth_settings.origin})

    @app.get('/api/admin/users')
    def list_users(request:Request):
        actor=require_admin(request)
        return invoke(service(request).list_users,actor['id'])

    @app.post('/api/admin/users',status_code=201)
    def create_user(body:NewUser,request:Request):
        require_csrf(request); actor=require_admin(request)
        return invoke(service(request).create_user,actor['id'],body.username,body.password,body.role)

    @app.patch('/api/admin/users/{user_id}')
    def update_user(user_id:str,body:UpdateUser,request:Request):
        require_csrf(request); actor=require_admin(request)
        if not body.model_fields_set or any(v is None for v in body.model_dump(exclude_unset=True).values()):
            raise HTTPException(400,'Invalid request')
        return invoke(service(request).update_user,actor['id'],user_id,**body.model_dump(exclude_unset=True))
