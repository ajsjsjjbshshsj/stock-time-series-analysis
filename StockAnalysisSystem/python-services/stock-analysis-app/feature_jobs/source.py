"""Bind metadata only; actual immutable raw snapshot belongs to the owned child."""
from datetime import date
from sqlalchemy import text
from tasks.contracts import digest
from .contracts import normalize


def code_identity():
    from tasks.source import APP
    names=['data_processor/feature_store_builder.py','data_processor/feature_store_source.py',
        'data_processor/feature_store_contract.py','data_processor/feature_schema.py',
        'data_processor/feature_engineer.py','database/feature_store_repository.py']
    names += [p.relative_to(APP).as_posix() for p in sorted((APP/'feature_jobs').glob('*.py'))]
    return digest({name:(APP/name).read_text(encoding='utf-8') for name in names})


class FeatureSource:
    def __init__(self,engine,calendar_path=None):
        from tasks.source import SourceProvider
        self.engine=engine;self.calendar_path=SourceProvider(engine,calendar_path).calendar_path

    def bind(self,value):
        request=normalize(value)
        from api.preparation import production_services
        from tasks.source import read_calendar
        from workflows.source import completed_session
        repo,pools,_=production_services(self.engine)
        parent=repo.get(request['preparation_id'])
        if parent['status']!='SUBMITTED' or parent['data_state']!='READY_CORE':
            raise ValueError('PREPARATION_NOT_READY')
        pool=pools.get(parent['pool_version'])
        if digest(pool)!=parent['pool_sha256']:raise ValueError('PREPARATION_POOL_CHANGED')
        calendar=read_calendar(self.calendar_path)
        completed=completed_session(calendar)
        with self.engine.connect() as c:
            active=c.execute(text("SELECT build_id,state,source_start,source_cutoff FROM stock_feature_build WHERE active_slot='traditional_v2'")).mappings().one_or_none()
        if active is not None and active['state']!='READY':raise ValueError('ACTIVE_FEATURE_NOT_READY')
        cutoff=max(parent['end_date'],str(active['source_cutoff']) if active else parent['end_date'])
        if date.fromisoformat(cutoff).isoformat()!=cutoff or not calendar['start']<='2023-01-01'<=cutoff<=completed:
            raise ValueError('FEATURE_RANGE_NOT_COMPLETED')
        return dict(preparation_id=parent['id'],preparation_sha256=parent['request_sha256'],
            preparation_batch_id=parent['batch_id'],pool_version=parent['pool_version'],
            pool_sha256=parent['pool_sha256'],pool_count=pool['stock_count'],pool_identity=pool['member_sha256'],
            source_start='2023-01-01',source_cutoff=cutoff,
            previous_active_build_id=active['build_id'] if active else None,
            calendar_sha256=digest(calendar),code_sha256=code_identity(),model_ready=False)

    def verify(self,task):
        from tasks.source import SourceChanged
        if self.bind(task['request'])!=task['binding']:raise SourceChanged('Feature build input binding changed')
