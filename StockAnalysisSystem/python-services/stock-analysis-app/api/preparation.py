"""Administrator fixed-pool preparation commands, never a source execution API."""
from fastapi import Request,HTTPException
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin,require_csrf
from api.collector import collector_imports,query,number,body,key,public_command


def production_services(engine):
    collector_imports()
    from sqlalchemy.orm import sessionmaker
    from app.repositories.web_preparation_repository import PreparationCommands
    from app.repositories.web_pool_repository import WebPools
    from app.repositories.web_collection_repository import CollectionCommands
    factory=sessionmaker(bind=engine)
    return PreparationCommands(factory),WebPools(factory),CollectionCommands(factory)


def invoke(operation,*args,**kwargs):
    try:return operation(*args,**kwargs)
    except LookupError:raise HTTPException(404,'Preparation or fixed pool not found') from None
    except ValueError as error:
        if str(error) in ('IDEMPOTENCY_CONFLICT','PREPARATION_NOT_RETRYABLE','TASK_NOT_RETRYABLE','RETRY_DATE_CHANGED'):
            raise HTTPException(409,'Preparation request conflict') from None
        raise HTTPException(400,'Invalid fixed-pool preparation request') from None
    except Exception:raise HTTPException(503,'Preparation dependencies unavailable; check explicit migration and worker') from None


def public(value):
    return {k:value[k] for k in ('id','pool_version','pool_sha256','registry_stock_count','start_date','end_date',
        'status','batch_id','planned_task_count','error_kind','retry_not_before','created_at','updated_at','task_count','task_states','data_state','model_ready')}


def register_preparation_routes(app):
    async def guard(request,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        actor=await run_in_threadpool(require_admin,request)
        if write:await run_in_threadpool(require_csrf,request)
        pair=getattr(app.state,'preparation_services',None)
        if pair is None:pair=await run_in_threadpool(invoke,production_services,app.state.auth_service.repo.engine)
        return pair,actor

    @app.get('/api/admin/preparation/requests')
    async def requests(request:Request,limit:str='20',offset:str='0'):
        (repo,_,_),_=await guard(request);query(request,{'limit','offset'})
        result=await run_in_threadpool(invoke,repo.list,number(limit,1,50),number(offset,0,100000))
        return dict(result,items=[public(row) for row in result['items']])

    @app.post('/api/admin/preparation/requests',status_code=202)
    async def submit(request:Request):
        (repo,pools,_),actor=await guard(request,True);query(request,set())
        request_key=key(request);value=await body(request,{'pool_version','start','end'})
        pool=await run_in_threadpool(invoke,pools.get,value['pool_version'])
        return public(await run_in_threadpool(invoke,repo.submit,actor['id'],request_key,pool,value['start'],value['end']))

    @app.get('/api/admin/preparation/requests/{id}')
    async def detail(id:str,request:Request):
        (repo,_,_),_=await guard(request);query(request,set())
        return public(await run_in_threadpool(invoke,repo.get,id))

    @app.get('/api/admin/preparation/requests/{id}/tasks')
    async def tasks(id:str,request:Request,limit:str='20',offset:str='0'):
        (repo,_,_),_=await guard(request);query(request,{'limit','offset'})
        return await run_in_threadpool(invoke,repo.children,id,number(limit,1,50),number(offset,0,100000))

    @app.post('/api/admin/preparation/requests/{id}/retry')
    async def retry(id:str,request:Request):
        (repo,_,_),_=await guard(request,True);query(request,set());await body(request,set())
        return public(await run_in_threadpool(invoke,repo.retry,id))

    @app.post('/api/admin/preparation/requests/{id}/tasks/{task_id}/retry',status_code=202)
    async def retry_task(id:str,task_id:str,request:Request):
        (repo,_,collection),actor=await guard(request,True);query(request,set());await body(request,set())
        request_key=key(request)
        child=await run_in_threadpool(invoke,repo.child,id,task_id)
        # Use the existing durable retry inbox. This links the original day task,
        # never re-plans a full-market batch or changes its fixed pool/date.
        import hashlib
        derived=hashlib.sha256(f'pool-preparation:{id}:{task_id}:{request_key}'.encode()).hexdigest()[:32]
        result=await run_in_threadpool(invoke,collection.submit,actor['id'],derived,child['trade_date'],child['trade_date'],target=task_id)
        return public_command(result)
