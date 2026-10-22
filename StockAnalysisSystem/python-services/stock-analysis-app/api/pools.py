"""Protected fixed pool control plane. No source requests, model imports or DDL."""
from pathlib import Path
import os
from fastapi import HTTPException,Request
from starlette.concurrency import run_in_threadpool
from api.auth.dependencies import require_admin,require_csrf
from api.collector import collector_imports,query,number,body


def production_services(engine):
    collector_imports()
    from sqlalchemy.orm import sessionmaker
    from app.repositories.web_pool_repository import WebPools
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    from app.repositories.source_evidence_repository import SourceEvidenceRepository
    from app.preparation.evidence import EvidenceStore
    from app.preparation.web_pool_coverage import PoolCoverage
    from app.preparation.calendar import CalendarStore
    from app.nightly.universe import latest_local_reference
    from app.runtime_paths import RuntimePaths
    project=Path(__file__).resolve().parents[3]
    paths=RuntimePaths.from_environment(project,os.environ)
    factory=sessionmaker(bind=engine)
    pools=WebPools(factory)
    def calendar(start,end,now):
        from tasks.source import SourceProvider,read_calendar
        configured=SourceProvider(engine).calendar_path
        if configured.exists():
            known=read_calendar(configured)
            if known['start']<=start<=end<=known['end']:return known
        return CalendarStore(paths.data_root,None,None).get(start,end,now,acquire=False)
    coverage=PoolCoverage(pools,GapCoverageRepository(factory),calendar_loader=calendar,
        reference_loader=lambda:latest_local_reference(paths.data_root,paths),
        evidence_store=EvidenceStore(paths.data_root,SourceEvidenceRepository(factory)))
    return pools,coverage


def invoke(operation,*args,**kwargs):
    from app.repositories.web_pool_repository import PoolConflict,PoolUnavailable
    from app.preparation.web_pool_coverage import CoverageUnavailable
    try:return operation(*args,**kwargs)
    except PoolConflict:raise HTTPException(409,'Fixed pool source changed; refresh preview') from None
    except PoolUnavailable:raise HTTPException(404,'Selected pool snapshot is unavailable or incomplete') from None
    except CoverageUnavailable:raise HTTPException(503,'Trusted coverage evidence unavailable') from None
    except ValueError:raise HTTPException(400,'Invalid pool selection') from None
    except Exception:raise HTTPException(503,'Pool dependencies unavailable; check database and local evidence') from None


def brief(pool):
    from app.repositories.web_pool_repository import summary
    return dict(pool=summary(pool),preview=pool['members'][:20],total=pool['stock_count'])


def register_pool_routes(app):
    async def guard(request,write=False):
        if app.state.auth_settings.mode!='protected':raise HTTPException(503,'Protected mode required')
        await run_in_threadpool(require_admin,request)
        if write:await run_in_threadpool(require_csrf,request)
        pair=getattr(app.state,'pool_services',None)
        if pair is None:
            try:pair=await run_in_threadpool(production_services,app.state.auth_service.repo.engine)
            except Exception:raise HTTPException(503,'Pool dependencies unavailable') from None
        return pair

    @app.get('/api/admin/pools/catalog')
    async def catalog(request:Request):
        pools,_=await guard(request);query(request,set())
        return await run_in_threadpool(invoke,pools.catalog)

    @app.get('/api/admin/pools/candidate')
    async def candidate(request:Request,kind:str='',code:str='',as_of:str|None=None):
        pools,_=await guard(request);query(request,{'kind','code','as_of'})
        return brief(await run_in_threadpool(invoke,pools.candidate,kind,code,as_of=as_of))

    @app.post('/api/admin/pools/snapshots',status_code=201)
    async def snapshot(request:Request):
        pools,_=await guard(request,True);query(request,set())
        value=await body(request,{'kind','code','as_of','expected_version'})
        return brief(await run_in_threadpool(invoke,pools.save,value['kind'],value['code'],value['expected_version'],as_of=value['as_of']))

    @app.get('/api/admin/pools/versions')
    async def versions(request:Request,limit:str='20',offset:str='0'):
        pools,_=await guard(request);query(request,{'limit','offset'})
        return await run_in_threadpool(invoke,pools.versions,number(limit,1,50),number(offset,0,100000))

    @app.get('/api/admin/pools/members')
    async def members(request:Request,version:str='',limit:str='20',offset:str='0'):
        pools,_=await guard(request);query(request,{'version','limit','offset'})
        return await run_in_threadpool(invoke,pools.members,version,limit=number(limit,1,50),offset=number(offset,0,100000))

    @app.get('/api/admin/pools/coverage')
    async def coverage(request:Request,version:str='',start:str='',end:str='',limit:str='20',offset:str='0'):
        _,coverage=await guard(request);query(request,{'version','start','end','limit','offset'})
        return await run_in_threadpool(invoke,coverage.inspect,version,start,end,limit=number(limit,1,50),offset=number(offset,0,100000))
