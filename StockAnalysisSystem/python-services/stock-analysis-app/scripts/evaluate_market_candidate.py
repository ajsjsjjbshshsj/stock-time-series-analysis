"""Explicit offline evaluation of one successful frozen all-market candidate.

No queue writes, online requests, torch imports, fits or publication. Each run
creates a new private evaluation directory; partial runs never get a seal.
"""
import argparse
import json,os,sys,time,uuid
from pathlib import Path

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id',required=True)
    parser.add_argument('--project-root',type=Path,default=APP.parents[1],help='Runtime/config project root; useful for an isolated checkout')
    parser.add_argument('--output-root',type=Path,default=Path('.runtime/training_tasks/evaluations'))
    args=parser.parse_args();project=args.project_root.resolve()
    from dotenv import load_dotenv
    load_dotenv(project/'.env',override=True)
    import pandas as pd
    import lightgbm as lgb
    from sqlalchemy import text
    from database.db_connector import DatabaseConnector
    from tasks.artifacts import task_path,read_json,file_digest,write_json
    from tasks.lightgbm_artifacts import verify_lightgbm
    from tasks.market_source import MarketSource
    from tasks.market_samples import open_days
    from tasks.source import read_calendar
    from tasks.market_profile import bounded_profile
    from tasks import resources
    from analysis.market_evaluation import check_binding,score_stock,load_factors,build_report,seal_evaluation,verify_evaluation
    # Append collector code from this checkout; never resolve a developer path.
    sys.path.append(str(APP.parent/'python-collector'))
    from app.runtime_paths import RuntimePaths
    from app.market_data.execution_evidence import ExecutionStore
    paths=RuntimePaths.from_environment(project,os.environ)
    candidate_root=Path(os.getenv('TRAINING_ARTIFACT_ROOT','.runtime/training_tasks/candidates'))
    if not candidate_root.is_absolute():candidate_root=project/candidate_root
    candidate=task_path(candidate_root,args.task_id)
    output=args.output_root if args.output_root.is_absolute() else project/args.output_root
    folder=task_path(output,uuid.uuid4().hex)
    for protected in (candidate_root,candidate_root.parent/'work',paths.data_root,project/'.git',project.parent/'.git'):
        a,b=folder.resolve(),protected.resolve()
        if a.is_relative_to(b) or b.is_relative_to(a):raise ValueError('Evaluation overlaps protected inputs')
    resources.preflight(folder,bounded_profile());started=time.monotonic()
    with DatabaseConnector(diagnostic=True) as db:
        with db.engine.connect() as c:
            row=c.execute(text('SELECT id,request,binding,state FROM analysis_training_task WHERE id=:id'),dict(id=args.task_id)).mappings().one()
        if row['state']!='SUCCEEDED':raise ValueError('Successful frozen candidate required')
        task=dict(id=row['id'],request=json.loads(row['request']),binding=json.loads(row['binding']))
        verified=verify_lightgbm(task,candidate_root)
        meta=read_json(candidate/'metadata.json');model=lgb.Booster(model_file=str(candidate/'model.txt'))
        calendar_path=Path(os.getenv('TRAINING_CALENDAR_FILE','.runtime/training_tasks/calendar.json'))
        if not calendar_path.is_absolute():calendar_path=project/calendar_path
        source=MarketSource(db.engine,calendar_path,evidence_root=paths.data_root)
        current=source.bind(task['request']);check_binding(task['binding'],current)
        calendar=read_calendar(calendar_path);opened=[str(d) for d in open_days(calendar)]
        begin=meta['splits']['test']['signal_start'];last=meta['splits']['test']['signal_end']
        scheduled=list(range(opened.index(begin),opened.index(last)+1,5))
        execution_end=opened[scheduled[-1]+6]
        if execution_end>task['binding']['source_cutoff']:raise ValueError('Incomplete execution tail')
        request=dict(task['request'],start_date=begin)
        evaluation_task=dict(task,request=request,binding=current)
        folder.mkdir(parents=True,exist_ok=False)
        for name in ('scores','prices','coverage'):(folder/name).mkdir()
        print(json.dumps(dict(stage='BOUND',output=str(folder),pool_count=current['pool_count'],signal_start=begin,signal_end=request['end_date'],execution_end=execution_end)),flush=True)
        scores=[];prices=[];suspended=[];count=0
        for code,stock,cal,proof in source.iter_stocks(evaluation_task):
            scored,coverage=score_stock(model,stock,cal,proof,request,meta['feature_names'])
            observed=stock.source_history
            observed=observed[observed.trade_date.astype(str).between(begin,execution_end)].copy()
            scored.to_parquet(folder/'scores'/(code+'.parquet'),index=False)
            observed.to_parquet(folder/'prices'/(code+'.parquet'),index=False)
            write_json(folder/'coverage'/(code+'.json'),coverage)
            scores.append(scored);prices.append(observed)
            suspended.extend((code,day) for day in proof['full_days'] if begin<=day<=execution_end)
            count+=1
            if count%50==0 or count==current['pool_count']:
                resources.check_time(time.monotonic()-started,bounded_profile())
                if resources.local_rss()>bounded_profile()['max_tree_rss_bytes']:raise ValueError('Evaluation memory limit')
                print(json.dumps(dict(stage='SCORE',completed=count,total=current['pool_count'],elapsed_seconds=round(time.monotonic()-started,1))),flush=True)
        scored=pd.concat(scores,ignore_index=True);observed=pd.concat(prices,ignore_index=True)
        factors,factor_refs=load_factors(source.evidence,current['stock_codes'],begin,execution_end)
        limits=[];store=ExecutionStore(paths.data_root)
        for day in opened:
            if begin<=day<=execution_end:limits.extend(store.load(day))
        limit_frame=pd.DataFrame(limits,columns=['ts_code','trade_date','up_limit','down_limit'])
        factors.to_parquet(folder/'factors.parquet',index=False);limit_frame.to_parquet(folder/'limits.parquet',index=False)
        backtest_scores=scored[pd.to_datetime(scored.trade_date)<=pd.Timestamp(last)].copy()
        def account_sink(name,account):
            target=folder/'accounts'/name;target.mkdir(parents=True,exist_ok=False)
            for key in ('equity','trades','orders'):pd.DataFrame(account[key]).to_parquet(target/(key+'.parquet'),index=False)
            write_json(target/'open_positions.json',account['open_positions'])
        report=build_report(observed,backtest_scores,factors,limit_frame,calendar,account_sink=account_sink,
            top_n=10,suspended=suspended,signal_start=begin,signal_end=last)
        pd.DataFrame(report.get('missing',[])).to_parquet(folder/'requirements.parquet',index=False)
        # Keep report JSON compact; all missing keys are preserved in the parquet.
        if 'missing' in report:
            report['missing_count']=len(report['missing']);report['missing']=report['missing'][:100]
        report.update(task_id=task['id'],candidate_manifest_sha256=verified['manifest_sha256'],pool_count=current['pool_count'],pool_identity=current['pool_identity'],
            scored_rows=len(scored),scored_stocks=count,signal_start=begin,signal_end=request['end_date'],backtest_signal_end=last,execution_end=execution_end,
            published=False,elapsed_seconds=time.monotonic()-started)
        write_json(folder/'binding.json',dict(training_binding=task['binding'],evaluation_binding=current,factor_references=factor_refs,
            evaluator_files={p.name:file_digest(p) for p in (APP/'analysis/market_evaluation.py',APP/'analysis/market_account.py',APP/'tasks/market_samples.py',Path(__file__))}))
        check_binding(task['binding'],source.bind(task['request']))
        if verify_lightgbm(task,candidate_root)!=verified:raise ValueError('Candidate changed during evaluation')
        seal_evaluation(folder,report);verify_evaluation(folder)
        print(json.dumps(dict(stage='DONE',status=report['status'],output=str(folder),scored_rows=len(scored),missing_count=report.get('missing_count',0),published=False)),flush=True)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:
        print(json.dumps(dict(status='EVALUATION_FAILED',error_type=type(exc).__name__,published=False)),file=sys.stderr)
        raise SystemExit(1)
