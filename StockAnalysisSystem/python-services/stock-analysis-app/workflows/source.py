"""Server-bound local inputs. This adapter never invokes a provider or a model."""
from pathlib import Path
import os
from tasks.source import SourceProvider,PROJECT,read_calendar,SourceChanged
from tasks.contracts import digest
from tasks.artifacts import read_json,task_path
from .contracts import normalize_operation
from .artifacts import ArtifactStore
from .repository import WorkflowRepository

MAX_BACKTEST_PRICE_ROWS=250000


def completed_session(calendar,now=None):
    """Strictly after Shanghai16:00; never infer exchange sessions from weekdays."""
    from datetime import datetime,time,timedelta,timezone
    zone=timezone(timedelta(hours=8));now=now or datetime.now(zone)
    if now.utcoffset() is None:raise ValueError('Aware request clock required')
    now=now.astimezone(zone)
    eligible=[r['cal_date'] for r in calendar['rows'] if r['is_open'] and
        datetime.combine(datetime.fromisoformat(r['cal_date']).date(),time(16),zone)<now]
    if not eligible:raise ValueError('No completed session')
    return eligible[-1]


def check_backtest_budget(pool_count,sessions):
    rows=pool_count*sessions
    if rows>MAX_BACKTEST_PRICE_ROWS:raise ValueError('Backtest observation budget exceeded; shorten explicit period')
    return rows


def history_fingerprint(frame,cutoff):
    """Canonical raw prefix, not a fitted statistic or an outcome filter."""
    import hashlib,pandas as pd
    from data_processor.feature_store_source import SOURCE_COLUMNS,canonical_bytes
    raw=frame.reindex(columns=SOURCE_COLUMNS).copy()
    raw['trade_date']=pd.to_datetime(raw.trade_date).dt.strftime('%Y-%m-%d')
    raw=raw[raw.trade_date<=cutoff].sort_values(['ts_code','trade_date'])
    if raw.empty or raw.duplicated(['ts_code','trade_date']).any():raise ValueError('Invalid historical prefix')
    digest=hashlib.sha256()
    for row in raw.itertuples(index=False,name=None):
        values={k:(None if pd.isna(v) else (str(v) if k in ('ts_code','trade_date') else float(v))) for k,v in zip(SOURCE_COLUMNS,row)}
        digest.update(canonical_bytes(values)+b'\n')
    return digest.hexdigest()


def artifact_root():
    root=Path(os.getenv('TRAINING_ARTIFACT_ROOT','.runtime/training_tasks/candidates'))
    return root if root.is_absolute() else PROJECT/root


def pipeline_identity(algorithm):
    from importlib.metadata import version
    from tasks.source import APP
    names=['workflows/training.py']
    if algorithm=='transformer':
        names+=['workflows/transformer.py',*[f'analysis/transformer_{n}.py' for n in ('trainer','features','model','utils','config','scoring')],
                'analysis/transformer_portfolio_signals.py','data_processor/probe_selection.py']
        packages=('torch','lightgbm','numpy','pandas','scikit-learn','TA-Lib')
    elif algorithm=='lightgbm':
        names+=['tasks/disk_matrix.py','tasks/market_samples.py','tasks/market_profile.py','analysis/disk_probe.py','data_processor/probe_rules.py']
        packages=('lightgbm','numpy','pandas','TA-Lib')
    elif algorithm=='xgboost':
        names+=['analysis/predictor.py','analysis/pipeline.py','analysis/strategy_samples.py']
        packages=('xgboost','numpy','pandas','TA-Lib')
    else:raise ValueError('Unknown model algorithm')
    return dict(sha256=digest({name:(APP/name).read_text(encoding='utf-8') for name in names}),
        dependencies={name:version(name) for name in packages})


def native_request(request,code=None):
    common=dict(start_date=request['start_date'],end_date=request['end_date'])
    if request['algorithm']=='lightgbm':return dict(kind='lightgbm_all_market',purpose='engineering_smoke',scope='all_shsz_a',profile='bounded_v1',**common)
    return dict(kind='xgboost_single',ts_code=code or request['ts_code'],n_estimators=20,max_depth=3,**common)


class WorkflowSource:
    def __init__(self,engine,calendar_path=None):
        self.engine=engine;self.base=SourceProvider(engine,calendar_path)
        self.calendar_path=self.base.calendar_path;self.store=ArtifactStore(artifact_root());self.repo=WorkflowRepository(engine)

    def model(self,id):
        row=self.repo.get_model(id)
        if row is None:raise ValueError('Registered model required')
        proof=self.store.verify(id)
        if proof['manifest_sha256']!=row['manifest_sha256'] or proof['index']['metadata']!=row['metadata']:
            raise ValueError('Model index and artifact differ')
        return row,read_json(self.store.path(id)/'metadata.json',max_bytes=8*1024*1024)

    def bind(self,value):
        request=normalize_operation(value)
        if request['operation']=='import':
            from .imports import bind_import
            return bind_import(request,self)
        calendar=read_calendar(self.calendar_path);completed=completed_session(calendar)
        if request['end_date']>completed:raise ValueError('Requested session is not completed')
        pipeline=pipeline_identity(request['algorithm'])
        selected=request
        model_sha=None
        if request['operation']!='train':
            row,meta=self.model(request['model_id'])
            if row['algorithm']!=request['algorithm']:raise ValueError('Model algorithm mismatch')
            if meta['binding'].get('model_pipeline') is not None and meta['binding']['model_pipeline']!=pipeline:
                raise ValueError('Model algorithm/dependencies changed; explicit retraining required')
            selected=dict(request,ts_code=meta['native']['ts_code'],stock_codes=meta['native']['stock_codes'])
            model_sha=row['manifest_sha256']
            if request['operation']=='backtest':
                from analysis.strategy_dates import validate_session_calendar
                opened=validate_session_calendar(calendar)
                available=min(self.base.describe()['source_cutoff'],completed);signals=[d for d in opened if d<=request['end_date']]
                if not signals:raise ValueError('Backtest outside calendar')
                last=opened.index(signals[-1])+meta['native']['holding_sessions']+1
                execution_end=min(available,opened[min(last,len(opened)-1)])
                selected=dict(selected,end_date=execution_end)
        if selected['algorithm']=='lightgbm':
            data=self.base.bind(native_request(selected))
        elif selected['algorithm']=='xgboost':data=self.base.bind(native_request(selected))
        else:
            bindings={code:self.base.bind(native_request(selected,code)) for code in selected['stock_codes']}
            first=next(iter(bindings.values()))
            data={k:first[k] for k in ('build_id','source_sha256','source_start','source_cutoff')}
            data.update(stocks=bindings,stock_codes=selected['stock_codes'],pool_count=len(bindings),pool_identity=digest(selected['stock_codes']))
        if request['model_id']:
            frozen=meta['binding']['data']
            pairs=[(frozen,data)]
            if 'stocks' in frozen:
                if set(frozen['stocks'])!=set(data.get('stocks',{})):raise ValueError('Model stock pool changed')
                pairs.extend((frozen['stocks'][code],data['stocks'][code]) for code in frozen['stocks'])
            for old,new in pairs:
                for key in ('storage_contract','algorithm_version','columns_sha256','code_sha256','pool_identity'):
                    if old.get(key)!=new.get(key):raise ValueError('Model schema or frozen pool changed')
            if frozen['source_sha256']!=data['source_sha256'] and not (self.store.path(request['model_id'])/'history.json').exists():
                raise ValueError('Model requires verified historical prefix before source extension')
            if request['ts_code'] and request['ts_code']!=meta['native']['ts_code']:raise ValueError('Model stock cannot be replaced')
            if request['stock_codes'] and request['stock_codes']!=meta['native']['stock_codes']:raise ValueError('Model pool cannot be replaced')
        from tasks.source import APP
        files={p.relative_to(APP).as_posix():p.read_text(encoding='utf-8') for p in sorted((APP/'workflows').glob('*.py'))}
        bound=dict(data=data,workflow_sha256=digest(files),model_manifest_sha256=model_sha)
        bound['model_pipeline']=pipeline
        if request['operation']=='backtest':bound['execution_end']=selected['end_date']
        for key in ('build_id','source_sha256','source_start','source_cutoff','pool_identity'):
            bound[key]=data[key]
        bound['pool_count']=data.get('pool_count',len(data['stock_codes']))
        if request['operation']=='backtest':
            sessions=sum(r['is_open'] for r in calendar['rows'] if request['start_date']<=r['cal_date']<=selected['end_date'])
            bound['backtest_price_rows_budget']=check_backtest_budget(bound['pool_count'],sessions)
        return bound

    def verify(self,task):
        if self.bind(task['request'])!=task['binding']:raise SourceChanged('Workflow source changed')

    def matrix_directory(self,task):
        # Disposable training input is not part of the deployable model. Avoid
        # hashing a full-market float matrix on every future model operation.
        return task_path(self.store.root.parent/'workflow_work',task['id'])

    def load_stock(self,task,code):
        r=task['request'];bound=task['binding']['data']
        data=bound['stocks'][code] if r['algorithm']=='transformer' else bound
        selected=dict(r,ts_code=code,end_date=task['binding'].get('execution_end',r['end_date']))
        result=self.base.load(dict(request=native_request(selected,code),binding=data))
        if r['model_id']:
            dependency=result[0].attrs['feature_store_source_history'].to_frame()
            self.verify_history(task,code,dependency)
        return result

    def verify_history(self,task,code,history):
        path=self.store.path(task['request']['model_id'])/'history.json'
        if path.exists():
            proof=read_json(path,max_bytes=4*1024*1024)
            actual=history_fingerprint(history,proof['cutoff'])
            if code in proof['prefixes'] and actual!=proof['prefixes'][code] and task['request']['end_date']<proof['cutoff']:
                # Historical inference may supply only an earlier prefix. Read
                # the full verified history for integrity, never to score it.
                actual=history_fingerprint(self.full_history(code),proof['cutoff'])
            if code not in proof['prefixes'] or actual!=proof['prefixes'][code]:
                raise ValueError('Model historical raw data changed')

    def full_history(self,code):
        from database.feature_store_reader import metadata_row,stock_source
        with self.engine.connect() as c,c.begin():
            frame,_=stock_source(c,code,metadata_row(c))
        return frame

    def load(self,task):
        import pandas as pd
        self.verify(task);r=task['request']
        if r['algorithm']=='xgboost':
            code=r['ts_code'] if r['operation']=='train' else self.model(r['model_id'])[1]['native']['ts_code']
            return self.load_stock(task,code)
        if r['algorithm']!='transformer':raise ValueError('Expected small stock source')
        codes=r['stock_codes'] if r['operation']=='train' else self.model(r['model_id'])[1]['native']['stock_codes']
        frames=[];histories=[]
        for code in codes:
            frame,calendar,contract=self.load_stock(task,code)
            history=frame.attrs['feature_store_source_history'].to_frame()
            frame.attrs={};frames.append(frame);histories.append(history)
        return pd.concat(frames,ignore_index=True),read_calendar(self.calendar_path),dict(source_history=pd.concat(histories,ignore_index=True))

    def iter_stocks(self,task):
        from tasks.market_source import MarketSource
        request=native_request(dict(task['request'],end_date=task['binding'].get('execution_end',task['request']['end_date'])))
        for code,snapshot,calendar,proof in MarketSource(self.engine,self.calendar_path).iter_stocks(dict(request=request,binding=task['binding']['data'])):
            if task['request']['model_id']:self.verify_history(task,code,snapshot.source_history)
            yield code,snapshot,calendar,proof

    def execution_inputs(self,task,prices):
        from tasks.market_evidence import SuspensionEvidence
        from analysis.market_evaluation import load_factors,load_limits
        from analysis.strategy_dates import validate_session_calendar
        evidence=SuspensionEvidence(self.engine)
        # SuspensionEvidence installs the collector's sibling import path.
        from app.market_data.execution_evidence import ExecutionStore
        codes=sorted(prices.ts_code.unique());dates=prices.trade_date.astype(str)
        begin,end=dates.min()[:10],dates.max()[:10];calendar=read_calendar(self.calendar_path)
        opened=[d for d in validate_session_calendar(calendar) if begin<=d<=end]
        factors,refs=load_factors(evidence,codes,begin,end);limits=load_limits(ExecutionStore(evidence.store.root),codes,opened)
        halts=[];reviews=evidence.disclosures.load()
        for code in codes:
            proof=evidence.for_stock(code,begin,end,reviews=reviews)
            halts.extend((code,d) for d in proof['full_days'])
        return factors,limits,calendar,halts,dict(factor_source_references=len(refs),observed_price_rows=len(prices))
