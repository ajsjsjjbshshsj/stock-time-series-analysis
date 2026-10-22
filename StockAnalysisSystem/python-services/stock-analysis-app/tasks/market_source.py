"""Immutable full-market identity and bounded single-stock verified reads."""
from importlib.metadata import version
import json
from pathlib import Path
from sqlalchemy import text
from .contracts import digest,normalize_request
from .source import SourceChanged,SourceProvider,read_calendar,APP
from .market_evidence import SuspensionEvidence,collector_root
from .market_lifecycle import bind_lifecycles


def market_identity():
    names=('tasks/market_source.py','tasks/market_evidence.py','tasks/market_lifecycle.py','tasks/source.py',
           'database/feature_store_reader.py','tasks/market_samples.py','tasks/disk_matrix.py',
           'tasks/market_profile.py','analysis/disk_probe.py','analysis/disk_ranking.py','data_processor/probe_rules.py',
           'tasks/lightgbm_runner.py','tasks/lightgbm_artifacts.py','tasks/resources.py')
    files={name:(APP/name).read_text(encoding='utf-8') for name in names}
    for name in ('app/preparation/evidence.py','app/repositories/source_evidence_repository.py',
                 'app/market_data/repair_contract.py','app/market_data/repair_acquisition.py',
                 'app/nightly/universe.py','app/nightly/symbol_periods.py','app/preparation/disclosure_evidence.py'):
        files['collector/'+name]=(collector_root()/name).read_text(encoding='utf-8')
    return digest(files)


class MarketSource:
    def __init__(self,engine,calendar_path=None,*,evidence_root=None,pool=None):
        self.engine=engine
        self.pool=pool
        if pool is not None:
            from workflows.pools import validate_selection
            from copy import deepcopy
            self.pool=deepcopy(validate_selection(pool))
        self.calendar_path=SourceProvider(engine,calendar_path).calendar_path
        self.evidence=SuspensionEvidence(engine,evidence_root)

    def _source(self):
        from database.feature_store_reader import metadata_row
        from data_processor.feature_store_contract import validate_stock_code
        with self.engine.connect() as c:
            meta=metadata_row(c)
            references=[dict(row) for row in c.execute(text('SELECT ts_code,name,industry,list_date FROM stock_basic ORDER BY ts_code')).mappings()]
        if meta['state']!='READY':raise ValueError('READY market source required')
        shards=meta['source_manifest']['shards']
        codes=sorted(validate_stock_code(s['ts_code']) for s in shards)
        if not codes or len(set(codes))!=len(codes) or len(codes)!=meta['stock_count']:
            raise ValueError('Invalid full market source identity')
        selected=[]
        for row in references:
            try:validate_stock_code(row['ts_code'])
            except ValueError:continue  # Other markets are not this A-share pool.
            row['list_date']=str(row['list_date']) if row['list_date'] is not None else None
            selected.append(row)
        if [r['ts_code'] for r in selected]!=codes:raise ValueError('Master and full feature pool differ')
        if self.pool is not None:
            from workflows.pools import validate_selection
            frozen=validate_selection(self.pool)['stock_codes']
            if not set(frozen)<=set(codes):raise ValueError('Fixed pool member not in feature source')
            codes=list(frozen)
            members=set(codes)
            selected=[row for row in selected if row['ts_code'] in members]
        return meta,codes,selected

    def describe(self):
        meta,codes,_=self._source();calendar=read_calendar(self.calendar_path)
        if calendar['start']>str(meta['source_start']):raise ValueError('Full calendar dependency prefix required')
        return dict(build_id=meta['build_id'],pool_count=len(codes),pool_identity=digest(codes),
            source_start=max(str(meta['source_start']),calendar['start']),
            source_cutoff=min(str(meta['source_cutoff']),calendar['end']))

    def bind(self,request):
        request=normalize_request(request)
        if request['kind']!='lightgbm_all_market':raise ValueError('Expected market task')
        meta,codes,references=self._source();calendar=read_calendar(self.calendar_path)
        start,end=str(meta['source_start']),str(meta['source_cutoff'])
        if not start<=request['start_date']<=request['end_date']<=end:raise ValueError('Task outside source')
        if calendar['start']>start or calendar['end']<request['end_date']:raise ValueError('Calendar dependency history incomplete')
        bound={k:str(meta[k]) for k in ('build_id','storage_contract','algorithm_version','columns_sha256',
                                       'code_sha256','source_sha256','source_start','source_cutoff')}
        bound.update(stock_codes=codes,stockid2idx={code:i for i,code in enumerate(codes)},pool_count=len(codes),
            pool_identity=digest(codes),source_manifest_sha256=digest(meta['source_manifest']),
            reference_sha256=digest(references),calendar_sha256=digest(calendar),runner_sha256=market_identity(),
            absence_evidence_sha256=self.evidence.identity_for_pool(codes,start,end),
            lifecycle=bind_lifecycles(self.evidence.store.root,references,request['end_date']),
            training_dependencies={name:version(name) for name in ('lightgbm','pandas','numpy','TA-Lib')})
        if self.pool is not None:bound['registry_pool']=self.pool
        return bound

    def _verify_binding(self,task):
        try:current=self.bind(task['request'])
        except ValueError:raise SourceChanged('Bound full market unavailable') from None
        if current!=task['binding']:raise SourceChanged('Bound full market changed')

    def iter_stocks(self,task):
        from database import feature_store_reader as reader
        from data_processor.feature_store_contract import CANONICAL_TABLE,date_only
        self._verify_binding(task)
        meta,codes,references=self._source();calendar=read_calendar(self.calendar_path)
        with self.engine.connect() as c:
            reader.validate_metadata(meta);reader.validate_table(c,CANONICAL_TABLE,meta['build_id'])
        request=task['request'];start,end=date_only(request['start_date']),date_only(request['end_date'])
        lifecycles=task['binding']['lifecycle']['stocks']
        reviews=self.evidence.disclosures.load()
        expected=(meta['build_id'],meta['source_sha256'])
        for code in codes:
            with self.engine.connect() as c,c.begin():
                current=c.execute(text("SELECT state,build_id,source_sha256 FROM stock_feature_build WHERE active_slot='traditional_v2'")).mappings().one()
                if current['state']!='READY' or (current['build_id'],current['source_sha256'])!=expected:
                    raise SourceChanged('Full market publication changed')
                snapshot=reader.read_stock_snapshot(c,code,meta,start,end)
            proof=self.evidence.for_stock(code,str(meta['source_start']),request['end_date'],reviews=reviews)
            proof['lifecycle']=lifecycles[code]
            proof['list_date']=lifecycles[code]['list_date']
            yield code,snapshot,calendar,proof
            del snapshot,proof
        self._verify_binding(task)
        with self.engine.connect() as c:reader.validate_table(c,CANONICAL_TABLE,meta['build_id'])
