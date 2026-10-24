"""Read-only proof of the corresponding full-market20:00 completion, never acquisition."""
from datetime import datetime,time,timedelta,timezone
import os
from tasks.contracts import digest,identity

TZ=timezone(timedelta(hours=8))


def due_session(calendar,now):
    if now.utcoffset() is None:raise ValueError('Aware daily clock required')
    now=now.astimezone(TZ);day=now.date().isoformat()
    if not calendar['start']<=day<=calendar['end']:raise ValueError('Current trusted calendar required')
    rows=[r for r in calendar['rows'] if r['cal_date']==day]
    if len(rows)!=1:raise ValueError('Current trusted session required')
    return day if rows[0]['is_open'] and now.time()>=time(20) else None


class NightlyGate:
    def __init__(self,engine):self.engine=engine

    def check(self,task_id,day):
        identity(task_id)
        from api.collector import collector_imports
        collector_imports()
        from app.runtime_paths import RuntimePaths
        from app.nightly.gap_store import GapBatchStore
        from app.repositories.market_update_repository import MarketUpdateRepository
        from sqlalchemy.orm import sessionmaker
        from tasks.source import PROJECT
        paths=RuntimePaths.from_environment(PROJECT,os.environ)
        task=MarketUpdateRepository(sessionmaker(bind=self.engine)).get(task_id)
        result=task['result'] or {};scheduled=datetime.fromisoformat(task['request']['scheduled_for']).astimezone(TZ)
        conflicts=result.get('conflicts');missing=result.get('unresolved_keys')
        # The gap collector seals detailed conflict records, not a count.
        # Preserve compatibility with historical integer0, never bool/float0.
        no_conflicts=(type(conflicts) is list and not conflicts) or (type(conflicts) is int and conflicts==0)
        if (task['trade_date']!=day or task['market_scope']!='SH_SZ_A' or task['status']!='SUCCEEDED'
                or scheduled.date().isoformat()!=day or scheduled.time()!=time(20)
                or result.get('data_ready') is not True or type(missing) is not int or missing!=0 or not no_conflicts):
            raise ValueError('Corresponding full-market nightly completion required')
        batch=GapBatchStore(paths.data_root).load(task['requirements_version']);snapshot=batch['snapshot']
        if snapshot.get('registry_pool') is not None or snapshot['sessions']!=[day]:
            raise ValueError('Selected or historical acquisition cannot qualify nightly')
        return dict(task_id=task_id,day=day,sha256=digest(dict(request=task['request'],result=result,batch_sha256=batch['full_sha256'])))

    def current(self,day):
        from sqlalchemy import text
        with self.engine.connect() as c:
            ids=c.execute(text("SELECT task_id FROM market_update_run WHERE trade_date=:day AND market_scope='SH_SZ_A' AND status='SUCCEEDED' AND requirements_version LIKE 'market-gap-v1:%' ORDER BY created_at DESC LIMIT 100"),dict(day=day)).scalars().all()
        for id in ids:
            try:return self.check(id,day)
            except ValueError:continue
        raise LookupError('Nightly full-market data not ready')
