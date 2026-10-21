"""Bounded indexes, artifact reads and publication; no native model imports."""
import re
from sqlalchemy import text
from tasks.contracts import identity,Conflict,NotFound
from tasks.artifacts import read_json
from .contracts import normalize_operation
from .repository import WorkflowRepository

SECTIONS=frozenset(('scores','equity','trades','orders','writeoffs','requirements'))


def sealed_result_context(metadata):
    """Public display fields from a verified seal, never today's source state."""
    from datetime import date
    binding=metadata.get('binding',{});data=binding.get('data',{})
    codes=data.get('stock_codes');count=binding.get('pool_count',data.get('pool_count'))
    if codes is not None:
        if (not isinstance(codes,list) or not codes or len(set(codes))!=len(codes)
                or any(not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ)',code) for code in codes)):
            raise ValueError('Invalid sealed pool')
        if count is None:count=len(codes)
        if count!=len(codes):raise ValueError('Sealed pool count mismatch')
    if count is not None and (type(count) is not int or not 1<=count<=10000):
        raise ValueError('Invalid sealed pool count')
    result=dict(scope='all_shsz_a' if metadata.get('index',{}).get('algorithm')=='lightgbm' else 'fixed_stocks',
        pool_count=count,stock_codes=list(codes) if codes is not None and len(codes)<=20 else None,
        provenance='sealed_binding')
    for key in ('source_start','source_cutoff'):
        value=binding.get(key,data.get(key))
        if value is not None and (not isinstance(value,str) or date.fromisoformat(value).isoformat()!=value):
            raise ValueError('Invalid sealed source date')
        result[key]=value
    for key,size in (('build_id',32),('pool_identity',64)):
        value=binding.get(key,data.get(key))
        if value is not None and (not isinstance(value,str) or not re.fullmatch('[0-9a-f]{'+str(size)+'}',value)):
            raise ValueError('Invalid sealed source identity')
        result[key]=value
    return result


def select_default_transformer_pool(shards,calendar,start,end):
    """Suggest only a data-complete pilot, never select by model score/returns.

    A suggestion is not permission to alter an already submitted frozen pool.
    Worker preflight still verifies every explicitly configured member.
    """
    opened=[r['cal_date'] for r in calendar['rows'] if r['is_open'] and start<=r['cal_date']<=end]
    if not opened:return []
    return sorted({s['ts_code'] for s in shards if re.fullmatch(r'[0-9]{6}\.(SH|SZ)',s.get('ts_code',''))
        and s.get('first_date')==opened[0] and s.get('last_date')==opened[-1]
        and type(s.get('row_count')) is int and s['row_count']==len(opened)})[:20]


class WorkflowService:
    def __init__(self,task_service,store):
        self.tasks=task_service;self.repo=WorkflowRepository(task_service.repo.engine);self.store=store

    def capabilities(self):
        self.repo.check_ready();source=self.tasks.source.describe();pool=[]
        from .source import completed_session,MAX_BACKTEST_PRICE_ROWS
        from tasks.source import read_calendar
        calendar=read_calendar(self.tasks.source.calendar_path);completed=completed_session(calendar)
        source=dict(source,stored_source_cutoff=source['source_cutoff'],source_cutoff=min(source['source_cutoff'],completed),completed_session=completed)
        try:
            import json
            with self.repo.engine.connect() as c:
                value=c.execute(text("SELECT source_manifest FROM stock_feature_build WHERE active_slot='traditional_v2' AND state='READY'")).scalar_one()
            body=json.loads(value) if isinstance(value,str) else value
            pool=select_default_transformer_pool(body['shards'],calendar,source['source_start'],source['stored_source_cutoff'])
        except Exception:pass  # Source.describe remains the authoritative readiness check.
        from tasks.market_profile import bounded_profile
        slot=self.tasks.slot_status()
        return dict(algorithms=['xgboost','lightgbm','transformer'],operations=['train','predict','backtest','import'],
            source=source,default_transformer_stocks=pool,transformer=dict(min_stocks=10,max_stocks=20,num_epochs=1,max_epochs=5,batch_size=1,max_batch_size=2,use_probe_selection=True),
            resource_profile=bounded_profile(),execution=dict(task_id=slot['task_id'],quarantined=slot['quarantined']),
            max_backtest_price_rows=MAX_BACKTEST_PRICE_ROWS,purpose='model_workflow',profit_required=False,automatic_publication=False)

    def create(self,actor,value,key):
        value=normalize_operation(value)
        if value['model_id'] and self.repo.get_model(value['model_id']) is None:raise NotFound('Model not found')
        return self.tasks.create(actor,value,key)

    def jobs(self,**kwargs):return self.tasks.list(**kwargs)

    def _verified(self,row,kind):
        if row is None:raise NotFound('Workflow artifact not found')
        try:
            proof=self.store.verify(row['id'])
            if (proof['manifest_sha256']!=row['manifest_sha256'] or proof['index']['artifact_kind']!=kind
                    or proof['index']['metadata']!=row['metadata'] or proof['index']['algorithm']!=row['algorithm']
                    or kind=='result' and proof['index']['model_id']!=row['model_id']):
                raise ValueError('Index binding changed')
        except Exception:raise RuntimeError('Workflow artifact unavailable') from None
        return row

    def models(self,**kwargs):
        value=self.repo.models(**kwargs)
        for row in value['items']:self._verified(row,'model')
        return value

    def results(self,*,public=False,**kwargs):
        value=self.repo.results(public=public,**kwargs)
        for row in value['items']:self._verified(row,'result')
        return value

    def result(self,id,*,public=False,section='scores',limit=50,after=0,ts_code=None):
        if section not in SECTIONS or type(limit) is not int or not 1<=limit<=100 or type(after) is not int or not 0<=after<=10000000:
            raise ValueError('Invalid result page')
        row=self._verified(self.repo.get_result(id,public=public),'result');folder=self.store.path(id)
        try:
            report=read_json(folder/'report.json',max_bytes=2*1024*1024)
            context=sealed_result_context(read_json(folder/'metadata.json',max_bytes=8*1024*1024))
            if report.get('status')!=row['metadata']['status']:raise ValueError('Result status differs')
            import pyarrow.dataset as ds
            if ts_code is not None:
                from data_processor.feature_store_contract import validate_stock_code
                validate_stock_code(ts_code)
            if section=='scores':
                latest=folder/'latest_scores.parquet'
                paths=[latest] if latest.exists() else sorted((folder/'scores').glob('*.parquet'))
            else:paths=[folder/(section+'.parquet')]
            paths=[p for p in paths if p.is_file()];rows=[];skipped=0
            if paths:
                dataset=ds.dataset([str(p) for p in paths],format='parquet')
                condition=ds.field('ts_code')==ts_code if ts_code is not None and 'ts_code' in dataset.schema.names else None
                scanner=dataset.scanner(filter=condition,batch_size=1024)
                for batch in scanner.to_batches():
                    if skipped+batch.num_rows<=after:skipped+=batch.num_rows;continue
                    values=batch.slice(max(0,after-skipped)).to_pylist();skipped+=batch.num_rows
                    rows.extend(values[:limit+1-len(rows)])
                    if len(rows)>limit:break
            return dict(**row,report=report,context=context,section=section,rows=rows[:limit],next_cursor=after+limit if len(rows)>limit else None)
        except Exception:raise RuntimeError('Workflow result unavailable') from None

    def publication(self,actor,id,published):
        row=self._verified(self.repo.get_result(id),'result')
        if published and row['metadata']['status']!='COMPLETE':raise Conflict('Result not complete')
        return self.repo.set_publication(actor,id,published)
