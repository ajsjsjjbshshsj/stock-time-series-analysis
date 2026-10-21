"""Server-bound local inputs. This adapter never invokes a provider or a model."""
from pathlib import Path
import os
from tasks.source import SourceProvider,PROJECT,read_calendar,SourceChanged
from tasks.contracts import digest
from tasks.artifacts import read_json,task_path
from .contracts import normalize_operation
from .artifacts import ArtifactStore
from .repository import WorkflowRepository


def artifact_root():
    root=Path(os.getenv('TRAINING_ARTIFACT_ROOT','.runtime/training_tasks/candidates'))
    return root if root.is_absolute() else PROJECT/root


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
        selected=request
        model_sha=None
        if request['operation']!='train':
            row,meta=self.model(request['model_id'])
            if row['algorithm']!=request['algorithm']:raise ValueError('Model algorithm mismatch')
            selected=dict(request,ts_code=meta['native']['ts_code'],stock_codes=meta['native']['stock_codes'])
            model_sha=row['manifest_sha256']
        if selected['algorithm']=='lightgbm':
            data=self.base.bind(native_request(selected))
        elif selected['algorithm']=='xgboost':data=self.base.bind(native_request(selected))
        else:
            bindings={code:self.base.bind(native_request(selected,code)) for code in selected['stock_codes']}
            first=next(iter(bindings.values()))
            data={k:first[k] for k in ('build_id','source_sha256','source_start','source_cutoff')}
            data.update(stocks=bindings,stock_codes=selected['stock_codes'],pool_count=len(bindings),pool_identity=digest(selected['stock_codes']))
        from tasks.source import APP
        files={p.relative_to(APP).as_posix():p.read_text(encoding='utf-8') for p in sorted((APP/'workflows').glob('*.py'))}
        bound=dict(data=data,workflow_sha256=digest(files),model_manifest_sha256=model_sha)
        for key in ('build_id','source_sha256','source_start','source_cutoff','pool_identity'):
            bound[key]=data[key]
        bound['pool_count']=data.get('pool_count',len(data['stock_codes']))
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
        selected=dict(r,ts_code=code)
        return self.base.load(dict(request=native_request(selected,code),binding=data))

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
        request=native_request(task['request'])
        yield from MarketSource(self.engine,self.calendar_path).iter_stocks(dict(request=request,binding=task['binding']['data']))
