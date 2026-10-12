"""Unified local stock-pool preparation CLI. No REST, training or automatic DDL."""
import argparse
import json
import math
import os
from pathlib import Path
import sys
import uuid

SERVICE=Path(__file__).resolve().parents[1]
ROOT=Path(__file__).resolve().parents[4]
PROJECT=ROOT/'StockAnalysisSystem'
ANALYSIS=PROJECT/'python-services/stock-analysis-app'
sys.path.insert(0,str(SERVICE))

def load_environment():
    """Configuration loading has no database, SDK or HTTP initialization."""
    from dotenv import load_dotenv
    for path in (SERVICE/'.env',PROJECT/'.env'):
        if path.is_file():load_dotenv(path);break


class Runtime:
    def __init__(self):
        load_environment()
        if str(ANALYSIS) not in sys.path: sys.path.append(str(ANALYSIS))
        from database.db_connector import DatabaseConnector
        from app.repositories.pool_repository import PoolRepository
        from app.repositories.preparation_repository import PreparationRepository
        from app.repositories.source_evidence_repository import SourceEvidenceRepository
        from app.repositories.preparation_import_repository import PreparationImportRepository
        from app.preparation.evidence import EvidenceStore
        from app.preparation.calendar import CalendarStore
        from app.preparation.acquisition import MarketAcquirer
        from app.preparation.reference import ReferenceLoader
        from app.preparation.service import DataPreparationService
        self.data_root=Path(os.getenv('PREPARATION_DATA_ROOT',str(PROJECT/'.runtime/stock_pool_preparation'))).resolve()
        interval=float(os.getenv('COLLECTION_REQUEST_INTERVAL','.5'))
        if not math.isfinite(interval) or interval<.5: raise ValueError('REQUEST_INTERVAL_INVALID')
        self.db=DatabaseConnector()
        self._http_session=None;self._tushare_client=None
        self._market_tasks=None;self._market_service=None;self._scheduler=None
        self.pools=PoolRepository(self.db.Session)
        self.tasks=PreparationRepository(self.db.Session)
        self.evidence=EvidenceStore(self.data_root,SourceEvidenceRepository(self.db.Session))
        self.acquirer=MarketAcquirer(self.client,PROJECT/'.runtime/market_requests/request_state.json',interval=interval)
        from app.preparation.acquisition import LazyClient
        # Initialize the transport only inside the shared pacing lock, including
        # calendar refreshes; a busy source must not initialize SDK/HTTP state.
        self.calendar=CalendarStore(self.data_root,self.acquirer.requests,lambda:LazyClient(self.client))
        self.service=DataPreparationService(self.pools,self.tasks,PreparationImportRepository(self.db.Session),
            self.evidence,self.calendar,self.acquirer,self.data_root,
            reference_loader=ReferenceLoader(self.client,self.acquirer.requests,self.db.Session))

    def client(self):
        if self._tushare_client is not None:return self._tushare_client
        from app.preparation.service import PreparationFailure
        try:
            from app.config import TUSHARE_TOKEN
        except EnvironmentError:
            raise PreparationFailure('PERMISSION') from None
        from app.market_data.tushare_client import TushareClient
        if self._http_session is None:
            import requests
            self._http_session=requests.Session()
        self._tushare_client=TushareClient.from_token(TUSHARE_TOKEN,timeout=20,session=self._http_session)
        return self._tushare_client

    def pool_sources(self):
        from app.preparation.pool_sources import PoolSources
        from app.market_data.akshare_client import AkshareClient
        from app.preparation.acquisition import LazyClient
        class LazyIndustry:
            def industry_constituents(self,code):
                import akshare
                return AkshareClient(akshare).industry_constituents(code)
        pacing=self.acquirer.requests(self.data_root/'pool_acquisition'/uuid.uuid4().hex)
        return PoolSources(pacing,LazyClient(self.client),LazyIndustry())

    def market_tasks(self):
        if self._market_tasks is None:
            from app.repositories.market_update_repository import MarketUpdateRepository
            self._market_tasks=MarketUpdateRepository(self.db.Session)
        return self._market_tasks

    def market_service(self):
        if self._market_service is None:
            from app.nightly.service import MarketUpdateService
            from app.nightly.universe import MarketUniverseProvider
            from app.nightly.acquisition import NativeDayAcquirer
            from app.nightly.reference import SnapshotReferenceWriter
            from app.repositories.preparation_import_repository import PreparationImportRepository
            self._market_service=MarketUpdateService(self.market_tasks(),PreparationImportRepository(self.db.Session),
                self.evidence,self.calendar,MarketUniverseProvider(self.client,self.acquirer.requests),
                NativeDayAcquirer(self.client,self.acquirer.requests),SnapshotReferenceWriter(self.db.Session),self.data_root)
        return self._market_service

    def scheduler(self,*,run_at='20:00',bootstrap_start=None):
        if self._scheduler is None:
            from app.nightly.scheduler import NightlyScheduler
            self._scheduler=NightlyScheduler(self.market_tasks(),
                lambda start,end,now:self.calendar.get(start,end,now,acquire=True),
                self.data_root,run_at=run_at,bootstrap_start=bootstrap_start)
        return self._scheduler

    def worker(self,*,nightly=False,run_at='20:00',bootstrap_start=None):
        from app.preparation.worker import PreparationWorker,WorkerQueue
        if not nightly:return PreparationWorker(self.tasks,self.service)
        from app.nightly.scheduler import PolicyBoundPreparationService
        scheduler=self.scheduler(run_at=run_at,bootstrap_start=bootstrap_start)
        return PreparationWorker(self.tasks,PolicyBoundPreparationService(self.service),
            queues=[WorkerQueue('market',self.market_tasks(),self.market_service())],before_poll=scheduler.tick)

    def close(self):
        try:self.db.close()
        finally:
            if self._http_session is not None:
                self._http_session.close();self._http_session=None;self._tushare_client=None


def public(value):
    if isinstance(value,dict):
        if value.get('scope')=='SH_SZ_A' and 'members' in value:
            return {k:public(value[k]) for k in ('scope','stock_count','member_sha256','acquired_at') if k in value}
        return {k:public(v) for k,v in value.items() if k not in {'owner_token','owner','active_identity','request_json','owner_json','request'}}
    if isinstance(value,list): return [public(v) for v in value]
    return value


def main(argv=None,dependencies=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('pool-import-legacy')
    listing=commands.add_parser('pool-list'); listing.add_argument('--pool-id')
    create=commands.add_parser('pool-create')
    create.add_argument('--type',choices=['index','industry'],required=True)
    create.add_argument('--code',required=True); create.add_argument('--as-of',required=True)
    create.add_argument('--new-version',action='store_true',help='Explicitly fetch/freeze a new snapshot; never overwrite old versions')
    evidence=commands.add_parser('evidence-import-legacy')
    evidence.add_argument('--source-id',choices=['csi300-b1-20261009'],required=True)
    for name in ('inspect','submit'):
        item=commands.add_parser(name)
        item.add_argument('--pool-version',required=True); item.add_argument('--start',required=True); item.add_argument('--end',required=True)
    for name in ('status','retry'):
        item=commands.add_parser(name); item.add_argument('--task-id',required=True)
    worker=commands.add_parser('worker'); worker.add_argument('--once',action='store_true')
    worker.add_argument('--nightly',action='store_true');worker.add_argument('--nightly-at');worker.add_argument('--bootstrap-start')
    nightly_submit=commands.add_parser('nightly-submit');nightly_submit.add_argument('--date',required=True)
    nightly_status=commands.add_parser('nightly-status');nightly_status.add_argument('--task-id')
    nightly_retry=commands.add_parser('nightly-retry')
    retry_group=nightly_retry.add_mutually_exclusive_group(required=True)
    retry_group.add_argument('--task-id');retry_group.add_argument('--scheduler',action='store_true')
    args=parser.parse_args(argv)
    run_at='20:00';bootstrap=None
    if args.command=='worker' and not args.nightly and (args.nightly_at is not None or args.bootstrap_start is not None):
        parser.error('--nightly-at/--bootstrap-start require --nightly')
    if args.command=='nightly-submit' or args.command=='worker' and args.nightly:
        if dependencies is None:
            try:load_environment()
            except Exception:parser.error('Cannot load project configuration')
        from app.nightly.contracts import market_day,parse_run_at
        from app.preparation.contracts import TZ
        from datetime import datetime
        run_at=(getattr(args,'nightly_at',None) or os.getenv('NIGHTLY_UPDATE_AT','20:00'))
        try:
            parse_run_at(run_at)
            if args.command=='nightly-submit':market_day(args.date)
            else:
                bootstrap=args.bootstrap_start or os.getenv('NIGHTLY_BOOTSTRAP_START')
                if bootstrap is not None:
                    if market_day(bootstrap)>datetime.now(TZ).date():raise ValueError('BOOTSTRAP_IN_FUTURE')
        except (ValueError,TypeError):parser.error('Invalid nightly time/date; time must be after 16:00 and bootstrap date >=2023-01-01, not future')
    runtime=None
    try:
        if dependencies is None:
            runtime=Runtime()
            deps={name:getattr(runtime,name) for name in ('pools','tasks','service','evidence','calendar','pool_sources','worker','market_tasks','market_service','scheduler')}
        else: deps=dependencies
        command=args.command
        if command=='pool-list': result=deps['pools'].list_versions(args.pool_id)
        elif command=='pool-import-legacy':
            from app.preparation.pool_sources import import_legacy_pool
            result=deps['pools'].register(import_legacy_pool(ANALYSIS/'models/universes/csi300_fixed/csi300_5739ddc829ad3650'),activate=True)
        elif command=='pool-create':
            pool_id=f'{args.type}:{args.code}'
            result=None
            if not args.new_version:
                try: result=deps['pools'].resolve(pool_id=pool_id)
                except ValueError as error:
                    if str(error)!='ACTIVE_POOL_NOT_FOUND': raise
            if result is None:
                result=deps['pools'].register(deps['pool_sources']().acquire(args.type,args.code,args.as_of),activate=True)
        elif command=='evidence-import-legacy':
            from app.market_data.forward_snapshot import read
            snapshot=PROJECT/'.runtime/csi300_repair/20261009_0930/snapshot'
            result=deps['evidence'].import_legacy_b1(snapshot)
            deps['calendar'].register(read(snapshot/'evidence/baseline/calendar.json'))
        elif command in ('inspect','submit'):
            request=dict(pool_version=args.pool_version,requested_start=args.start,requested_end=args.end,requirements_version='market-v1')
            result=getattr(deps['service'],command)(request)
        elif command=='status': result=deps['tasks'].get(args.task_id)
        elif command=='retry':
            from datetime import datetime,timezone
            result=deps['tasks'].retry(args.task_id,now=datetime.now(timezone.utc).isoformat())
        elif command=='nightly-submit':result=deps['market_service']().submit(args.date,run_at=run_at)
        elif command=='nightly-status':
            if args.task_id is not None:result=deps['market_tasks']().get(args.task_id)
            else:
                from datetime import datetime
                from app.preparation.contracts import TZ
                from app.nightly.contracts import START
                control=deps['scheduler']().status();end=datetime.now(TZ).date().isoformat()
                start=control['start_date'] or START
                result=dict(scheduler=control,runs=deps['market_tasks']().list_runs(start,end) if start<=end else [])
        elif command=='nightly-retry':
            if args.task_id is not None:
                from datetime import datetime,timezone
                result=deps['market_tasks']().retry(args.task_id,now=datetime.now(timezone.utc).isoformat())
            else:
                with deps['worker']().maintenance_lock():
                    scheduler=deps['scheduler']();scheduler.reset_error();result=scheduler.status()
        else:
            process=deps['worker'](nightly=True,run_at=run_at,bootstrap_start=bootstrap) if args.nightly else deps['worker']()
            result=process.run_once() if args.once else process.run_forever()
            if result is None: result=dict(status='IDLE' if args.once else 'STOPPED')
        print(json.dumps(public(result),ensure_ascii=False,allow_nan=False))
        return 1 if isinstance(result,dict) and result.get('status') in {'FAILED','PARTIAL','WAITING_RETRY'} else 0
    except KeyboardInterrupt:
        print(json.dumps(dict(status='STOPPED',resume_required=True))); return 130
    except Exception as error:
        from app.repositories.preparation_repository import ERRORS
        kind=getattr(error,'kind',None)
        if kind not in ERRORS: kind=str(error) if str(error) in ERRORS|{'MIGRATION_REQUIRED','WORKER_BUSY'} else 'PREPARATION_FAILED'
        print(json.dumps(dict(error_kind=kind),ensure_ascii=False)); return 2
    finally:
        if runtime is not None: runtime.close()


if __name__=='__main__': raise SystemExit(main())
