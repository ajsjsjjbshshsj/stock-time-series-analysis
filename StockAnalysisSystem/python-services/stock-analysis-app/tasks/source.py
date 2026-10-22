"""Resolve bounded immutable task inputs. No network, acquisition or model imports."""
from datetime import date,timedelta
import json
import os
from pathlib import Path
from sqlalchemy import text
from .contracts import digest,normalize_request

APP=Path(__file__).resolve().parents[1]
PROJECT=APP.parents[1]


class SourceChanged(ValueError): pass


def runner_identity():
    paths=('tasks/runner.py','tasks/artifacts.py','tasks/source.py','analysis/predictor.py',
           'analysis/strategy_samples.py','analysis/pipeline.py','analysis/feature_store_inputs.py')
    return digest({p:(APP/p).read_text(encoding='utf-8') for p in paths})


def read_calendar(path):
    path=Path(path)
    if path.is_symlink():raise ValueError('Unsafe calendar')
    with path.open('rb') as stream:raw=stream.read(256*1024+1)
    if len(raw)>256*1024:raise ValueError('Calendar too large')
    body=json.loads(raw)
    if body.get('source')!='tushare.trade_cal' or body.get('exchange')!='SSE':raise ValueError('Trusted calendar required')
    start,end=date.fromisoformat(body['start']),date.fromisoformat(body['end'])
    if not 0<=(end-start).days<=10000:raise ValueError('Calendar span invalid')
    days=[str(start+timedelta(days=i)) for i in range((end-start).days+1)]
    if 'rows' in body:rows=body['rows']
    else:
        opened=body['open_dates']
        if not isinstance(opened,list) or opened!=sorted(set(opened)) or any(d not in days for d in opened):raise ValueError('Calendar dates invalid')
        rows=[dict(cal_date=d,is_open=int(d in opened)) for d in days]
    if (not isinstance(rows,list) or len(rows)!=len(days) or any(
            not isinstance(r,dict) or set(r)!={'cal_date','is_open'} or r['cal_date']!=d
            or type(r['is_open']) is not int or r['is_open'] not in (0,1) for r,d in zip(rows,days))):
        raise ValueError('Calendar coverage invalid')
    return dict(source='tushare.trade_cal',exchange='SSE',start=str(start),end=str(end),rows=rows)


class SourceProvider:
    def __init__(self,engine,calendar_path=None):
        self.engine=engine
        configured=Path(calendar_path or os.getenv('TRAINING_CALENDAR_FILE','.runtime/training_tasks/calendar.json'))
        self.calendar_path=configured if configured.is_absolute() else PROJECT/configured

    def describe(self):
        calendar=read_calendar(self.calendar_path)
        with self.engine.connect() as c:
            row=c.execute(text('SELECT state,build_id,source_start,source_cutoff FROM stock_feature_build WHERE active_slot=:slot'),dict(slot='traditional_v2')).mappings().one_or_none()
        if row is None or row['state']!='READY':raise ValueError('READY source required')
        start=max(str(row['source_start']),calendar['start']);end=min(str(row['source_cutoff']),calendar['end'])
        if start>end:raise ValueError('No covered source range')
        return dict(build_id=row['build_id'],source_start=start,source_cutoff=end)

    def describe_market(self):
        from .market_source import MarketSource
        return MarketSource(self.engine,self.calendar_path).describe()

    def bind(self,request):
        request=normalize_request(request)
        if request['kind']=='feature_build':
            from feature_jobs.source import FeatureSource
            return FeatureSource(self.engine,self.calendar_path).bind(request)
        if request['kind']=='model_workflow':
            from workflows.source import WorkflowSource
            return WorkflowSource(self.engine,self.calendar_path).bind(request)
        if request['kind']=='lightgbm_all_market':
            from .market_source import MarketSource
            return MarketSource(self.engine,self.calendar_path).bind(request)
        calendar=read_calendar(self.calendar_path)
        if not calendar['start']<=request['start_date']<=request['end_date']<=calendar['end']:
            raise ValueError('Calendar does not cover task')
        with self.engine.connect() as c:
            row=c.execute(text('SELECT * FROM stock_feature_build WHERE active_slot=:slot'),dict(slot='traditional_v2')).mappings().one_or_none()
        if row is None or row['state']!='READY':raise ValueError('READY source required')
        if not str(row['source_start'])<=request['start_date']<=request['end_date']<=str(row['source_cutoff']):
            raise ValueError('Task outside source bounds')
        manifest=row['source_manifest'];manifest=json.loads(manifest) if isinstance(manifest,str) else manifest
        selected=[s for s in manifest['shards'] if s['ts_code']==request['ts_code']]
        if len(selected)!=1:raise ValueError('Selected stock not in source')
        history_start=selected[0].get('first_date',str(row['source_start']))
        if calendar['start']>history_start:raise ValueError('Calendar must cover indicator dependency history')
        bound={k:str(row[k]) for k in ('build_id','storage_contract','algorithm_version','columns_sha256',
                                      'code_sha256','source_sha256','source_start','source_cutoff')}
        bound.update(ts_code=request['ts_code'],stock_codes=[request['ts_code']],
            pool_identity=digest([request['ts_code']]),stockid2idx={request['ts_code']:0},
            source_evidence=selected[0],calendar_sha256=digest(calendar),runner_sha256=runner_identity())
        from importlib.metadata import version
        bound['training_dependencies']={name:version(name) for name in ('xgboost','pandas','numpy','TA-Lib')}
        return bound

    def load(self,task):
        request=normalize_request(task['request']);bound=task['binding']
        if self.bind(request)!=bound:raise SourceChanged('Bound source changed')
        from sqlalchemy.orm import Session
        from database.feature_store_reader import load_feature_snapshot
        from analysis.feature_store_inputs import build_research_inputs,validate_research_calendar
        with Session(self.engine) as session,session.begin():
            snapshot=load_feature_snapshot(session,[request['ts_code']],request['start_date'],request['end_date'])
        for key in ('build_id','storage_contract','algorithm_version','columns_sha256','code_sha256','source_sha256','source_start','source_cutoff'):
            if snapshot.metadata[key]!=bound[key]:raise SourceChanged('Read source differs from task')
        if snapshot.metadata['source_evidence'][request['ts_code']]!=bound['source_evidence']:
            raise SourceChanged('Stock evidence changed')
        calendar=read_calendar(self.calendar_path)
        if digest(calendar)!=bound['calendar_sha256']:raise SourceChanged('Calendar changed')
        history=snapshot.source_history
        validate_research_calendar(history[history.trade_date.astype(str)<=request['end_date']],calendar)
        frame,contract=build_research_inputs(snapshot,snapshot.raw_prices,
            dict(stock_codes=bound['stock_codes'],pool_identity=bound['pool_identity'],stockid2idx=bound['stockid2idx']))
        validate_research_calendar(frame,calendar)
        return frame,calendar,contract
