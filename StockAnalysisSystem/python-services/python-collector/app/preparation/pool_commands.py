"""Plan fixed-pool commands under the original collector's exclusive root lock."""
from datetime import datetime,timezone
from functools import reduce
from operator import or_
from pathlib import Path
import time
from app.preparation.contracts import identity
from app.preparation.gap_plan import eligible_bits
from app.preparation.worker import require_root_lock
from app.preparation.web_commands import retry_deadline
from app.repositories.preparation_repository import ERRORS
from app.repositories._preparation_sql import stamp
from app.market_data.retry_policy import RETRYABLE_TASK_ERRORS


class PoolCommandProcessor:
    def __init__(self,repository,service,pools,*,clock=time.time):
        self.repo,self.service,self.pools,self.clock=repository,service,pools,clock

    def tick(self):
        require_root_lock(self.service.data_root)
        now=datetime.fromtimestamp(self.clock(),timezone.utc)
        try:command=self.repo.pending(now)
        except RuntimeError as error:
            if str(error)=='MIGRATION_REQUIRED':return
            raise
        if command is None:return
        try:
            pool=self.pools.resolve(pool_version=command['pool_version'])
            if identity(pool)!=command['pool_sha256']:raise ValueError('POOL_IDENTITY_CHANGED')
            if command['batch_id'] is None:
                batch=self.service.prepare_batch(command['start_date'],command['end_date'],now=now,pool=pool)
                self.repo.patch(command['id'],now=now,batch_id=batch['batch_id'])
            else:batch=self.service.batch_store.load(command['batch_id'])
            snapshot=batch['snapshot']
            if (snapshot.get('registry_pool')!=pool or snapshot['start']!=command['start_date'] or snapshot['end']!=command['end_date']):
                raise ValueError('POOL_BATCH_IDENTITY_CHANGED')
            active=reduce(or_,eligible_bits(snapshot).values(),0)
            self.repo.patch(command['id'],now=now,planned_task_count=active.bit_count())
            for index,day in enumerate(snapshot['sessions']):
                if not active&(1<<index):continue
                scheduled=datetime.fromisoformat(day+'T16:01:00+08:00')
                if now<scheduled:
                    self.repo.patch(command['id'],now=now,status='WAITING_RETRY',error_kind=None,retry_not_before=stamp(scheduled));return
                task=self.service.tasks.submit_bound(day,batch,scheduled_for=scheduled.isoformat(),now=now.isoformat())
                self.repo.link(command['id'],task['task_id'])
            self.repo.patch(command['id'],now=now,status='SUBMITTED',error_kind=None,retry_not_before=None)
        except Exception as error:
            kind=getattr(error,'kind',None)
            if kind not in ERRORS:kind=str(error) if str(error) in ERRORS else 'SCHEMA'
            waiting=kind in RETRYABLE_TASK_ERRORS
            deadline=retry_deadline(getattr(error,'retry_not_before',None),now) if waiting else None
            self.repo.patch(command['id'],now=now,status='WAITING_RETRY' if waiting else 'FAILED',error_kind=kind,
                retry_not_before=stamp(deadline) if deadline is not None else None)
