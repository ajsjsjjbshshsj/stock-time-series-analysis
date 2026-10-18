"""Plan first; --acquire explicitly fetches only still-missing execution data."""
import argparse,json,os,sys
from pathlib import Path
SERVICE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SERVICE))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requirements',type=Path,required=True)
    parser.add_argument('--project-root',type=Path,default=SERVICE.parents[1])
    parser.add_argument('--acquire',action='store_true',help='Opt in to real provider calls; default is a no-network plan')
    args=parser.parse_args();project=args.project_root.resolve()
    from dotenv import load_dotenv
    load_dotenv(project/'.env',override=True)
    from app.market_data.execution_requirements import read_requirements
    from app.market_data.execution_evidence import ExecutionStore,acquire_limits,known
    from app.runtime_paths import RuntimePaths
    from app.market_data.forward_snapshot import digest
    frame=read_requirements(args.requirements)
    paths=RuntimePaths.from_environment(project,os.environ);store=ExecutionStore(paths.data_root)
    limit_rows=frame[frame.kind=='stk_limit'];limit_plan={}
    for day,group in limit_rows.groupby('trade_date',sort=True):
        present={r['ts_code'] for r in store.load(day) if known(r)}
        missing=sorted(set(group.ts_code)-present)
        if missing:limit_plan[day]=missing
    summary=dict(limit_days=len(limit_plan),limit_keys=sum(map(len,limit_plan.values())),factor_keys=int((frame.kind=='adj_factor').sum()))
    print(json.dumps(dict(stage='PLAN',dry_run=not args.acquire,**summary)),flush=True)
    if not args.acquire:return
    # Shared provider limiter is constructed before the checked client; no new
    # background/nightly worker is started and no existing paused batch resumed.
    sys.path.append(str(SERVICE.parent/'stock-analysis-app'))
    from database.db_connector import DatabaseConnector
    from app.repositories.source_evidence_repository import SourceEvidenceRepository
    from app.preparation.evidence import EvidenceStore
    from app.preparation.acquisition import MarketAcquirer,LazyClient
    from app.market_data.tushare_client import TushareClient
    from app.market_data.repair_contract import request
    def client():
        token=os.getenv('TUSHARE_TOKEN')
        if not token:raise ValueError('TUSHARE_TOKEN_REQUIRED')
        return TushareClient.from_token(token,timeout=20)
    job=paths.data_root/'execution_acquisition'/digest(args.requirements)
    acquirer=MarketAcquirer(client,project/'.runtime/market_requests/request_state.json',
        interval=float(os.getenv('COLLECTION_REQUEST_INTERVAL','.5')))
    unresolved=[]
    for day,codes in limit_plan.items():
        pacing=acquirer.requests(job/'limits'/day)
        result=acquire_limits(day,codes,LazyClient(client),pacing,store)
        unresolved.extend(dict(kind='stk_limit',ts_code=c,trade_date=day) for c in result['missing_codes'])
        print(json.dumps(dict(stage='LIMITS',day=day,unresolved=len(result['missing_codes']))),flush=True)
    with DatabaseConnector(diagnostic=True) as db:
        evidence=EvidenceStore(paths.data_root,SourceEvidenceRepository(db.Session))
        for code,group in frame[frame.kind=='adj_factor'].groupby('ts_code',sort=True):
            start,end=group.trade_date.min(),group.trade_date.max()
            refs=evidence.repository.list('adj_factor',[code],start,end)
            present={r['trade_date'] for r in evidence.load(refs,codes=[code],start=start,end=end)['adj_factor']}
            for day in sorted(set(group.trade_date)-present):
                r=request('adj_factor',[code],day,day)
                result=acquirer.collect([r],job/'factors'/r['id'])
                shard=result['shards'][0]
                if not shard['rows']:
                    unresolved.append(dict(kind='adj_factor',ts_code=code,trade_date=day));continue
                evidence.register_verified(dict(kind='adj_factor',provider='tushare.adj_factor',start_date=day,end_date=day,
                    expected_codes=[code],rows=shard['rows']))
    print(json.dumps(dict(stage='DONE',status='NOT_READY' if unresolved else 'COMPLETE',unresolved=len(unresolved),published=False)),flush=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:
        print(json.dumps(dict(status='COLLECTION_STOPPED',error_type=type(exc).__name__,kind=getattr(exc,'kind',None))),file=sys.stderr)
        raise SystemExit(1)
