"""Staged CSI300 historical source repair; no training, replay or schema writes."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

SERVICE=Path(__file__).resolve().parents[1]
ROOT=Path(__file__).resolve().parents[4]
sys.path.insert(0,str(SERVICE))
from app.market_data.repair_contract import verify_pool, write_baseline, load_baseline, plan_requests, iso
from app.market_data.repair_acquisition import collect_requests, verify_acquisition
from app.market_data.repair_snapshot import assemble_snapshot, verify_snapshot
from app.market_data.forward_snapshot import read, digest

APP=ROOT/'StockAnalysisSystem/python-services/stock-analysis-app'
DEFAULT_POOL=APP/'models/universes/csi300_fixed/csi300_5739ddc829ad3650'
DEFAULT_OUTPUT=ROOT/'StockAnalysisSystem/.runtime/csi300_repair/20261009_0930'


def _repository():
    # Existing shared connector only, explicitly no create_tables().
    if str(APP) not in sys.path: sys.path.append(str(APP))
    from database.db_connector import DatabaseConnector
    from app.repositories.market_repair_repository import MarketRepairRepository
    db=DatabaseConnector()
    return MarketRepairRepository(db.Session)


def _calendar():
    path=APP/'models/universes/csi300_readiness/20261009_historical_0930'
    seal=read(path/'seal.json')
    if read(path/'complete.json')!=dict(schema_version=1,seal_sha256=digest(path/'seal.json')):
        raise ValueError('Calendar origin completion invalid')
    actual={p.relative_to(path).as_posix():digest(p) for p in path.rglob('*') if p.is_file() and p.name not in ()}
    actual.pop('seal.json'); actual.pop('complete.json')
    if seal['files']!=actual: raise ValueError('Calendar origin SHA invalid')
    return dict(read(path/'context.json')['calendar'],origin_context_sha256=digest(path/'context.json'),
                origin_seal_sha256=digest(path/'seal.json'))


def _client():
    import tushare as ts
    from app.config import TUSHARE_TOKEN
    from app.market_data.tushare_client import TushareClient
    return TushareClient(ts.pro_api(TUSHARE_TOKEN,timeout=20))


class LazyClient:
    def __init__(self,factory): self.factory=factory; self.client=None
    def __getattr__(self,name):
        def invoke(**params):
            if self.client is None: self.client=self.factory()
            return getattr(self.client,name)(**params)
        return invoke


def _interval():
    from dotenv import load_dotenv
    for path in (SERVICE/'.env',ROOT/'StockAnalysisSystem/.env'):
        if path.exists(): load_dotenv(path); break
    return float(os.getenv('COLLECTION_REQUEST_INTERVAL','.5'))


def run_stage(stage: str,root: Path,pool: Path,output: Path,start: str,end: str,dependencies: dict | None=None) -> dict:
    if stage not in ('inventory','acquire','assemble','import','verify'):
        raise ValueError('Unknown source repair stage')
    if not iso('2023-10-09')<=iso(start)<=iso(end)<=iso('2026-09-30'):
        raise ValueError('Outside approved historical window')
    root,pool,output=map(lambda p:Path(p).resolve(),(root,pool,output))
    deps=dependencies if dependencies is not None else {}
    if dependencies is None and (root!=ROOT.resolve() or pool!=DEFAULT_POOL.resolve()
        or not output.is_relative_to(root/'StockAnalysisSystem/.runtime/csi300_repair')):
        raise ValueError('Canonical root/frozen pool/repair output required')
    manifest=verify_pool(pool); base=output/'baseline'; acq=output/'acquisition'; snapshot=output/'snapshot'
    if stage=='inventory':
        if base.exists(): return dict(stage=stage,baseline=load_baseline(base)['manifest'],model_ready=False)
        calendar=deps.get('calendar_factory',_calendar)()
        tables=deps.get('repo_factory',_repository)().capture_baseline(manifest['codes'],start,end)
        return dict(stage=stage,baseline=write_baseline(base,pool,calendar,tables),model_ready=False)
    if stage in ('acquire','assemble'):
        data=load_baseline(base)
        if (data['manifest']['binding']['pool_sha256']!=digest(pool/'manifest.json')
            or (data['tables']['start'],data['tables']['end'])!=(start,end)):
            raise ValueError('Repair pool/window differs')
    if stage=='acquire':
        initial=plan_requests(base)
        clock,sleep=deps.get('clock',time.time),deps.get('sleep',time.sleep)
        state=deps.get('request_state',root/'StockAnalysisSystem/.runtime/market_requests/request_state.json')
        client=LazyClient(deps.get('client_factory',_client))
        interval=deps.get('interval',.5) if dependencies is not None else _interval()
        first=collect_requests(client,initial,acq/'initial',state,interval,clock,sleep)
        if not first['complete']: return dict(stage=stage,acquisition_complete=False,status=first['status'],model_ready=False)
        quotes=[]
        for shard in first['shards']:
            if shard['request']['method']=='daily':
                for row in shard['rows']:
                    day=row['trade_date']; quotes.append(dict(row,trade_date=f'{day[:4]}-{day[4:6]}-{day[6:]}'))
        queried={r['start'] for r in initial if r['method']=='daily_basic'}
        additional=[r for r in plan_requests(base,quotes) if r['method']=='daily_basic' and r['start'] not in queried]
        second=collect_requests(client,additional,acq/'basic',state,interval,clock,sleep)
        return dict(stage=stage,acquisition_complete=second['complete'],request_count=len(initial)+len(additional),
                    status='COMPLETE' if second['complete'] else second['status'],model_ready=False)
    if stage=='assemble': return dict(stage=stage,**assemble_snapshot(base,acq,snapshot)['report'])
    verified=verify_snapshot(snapshot)
    source=load_baseline(snapshot/'evidence/baseline')
    if source['manifest']['binding']['pool_sha256']!=digest(pool/'manifest.json') or (source['tables']['start'],source['tables']['end'])!=(start,end):
        raise ValueError('Snapshot pool/window differs')
    if stage=='verify': return dict(stage=stage,**verified['report'])
    return deps.get('repo_factory',_repository)().import_snapshot(snapshot,output/'import')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['inventory','acquire','assemble','import','verify'],required=True)
    parser.add_argument('--pool',type=Path,default=DEFAULT_POOL)
    parser.add_argument('--output',type=Path,default=DEFAULT_OUTPUT)
    parser.add_argument('--start',default='2023-10-09'); parser.add_argument('--end',default='2026-09-30')
    parser.add_argument('--allow-insert',action='store_true'); parser.add_argument('--verify-db',action='store_true')
    args=parser.parse_args(argv)
    if args.stage=='import' and not args.allow_insert:
        print('IMPORT_REFUSED: explicit --allow-insert required',file=sys.stderr); return 2
    if args.verify_db and args.stage!='verify':
        print('VERIFY_DB_REFUSED: use verify stage',file=sys.stderr); return 2
    try:
        result=run_stage(args.stage,ROOT,args.pool,args.output,args.start,args.end)
        if args.verify_db: result['database']=_repository().verify_import(args.output/'snapshot',args.output/'import')
        print(json.dumps(result,ensure_ascii=False,allow_nan=False))
        incomplete=result.get('acquisition_complete') is False or result.get('db_import_complete') is False or result.get('database',{}).get('db_import_complete') is False
        return 1 if incomplete else 0
    except Exception:
        print('CSI300_REPAIR_FAILED: inspect source evidence, shared quota deadline and import ledger; credentials hidden',file=sys.stderr)
        return 1


if __name__=='__main__': sys.exit(main())
