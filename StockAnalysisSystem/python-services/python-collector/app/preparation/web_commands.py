"""Drain web commands ONLY inside the existing collector exclusive root lock."""
from datetime import datetime, timezone, timedelta
import math
import time
from app.preparation.worker import require_root_lock
from app.preparation.service import PreparationFailure
from app.repositories._preparation_sql import stamp
from app.repositories.preparation_repository import ERRORS
from app.market_data.retry_policy import RETRYABLE_TASK_ERRORS


def retry_deadline(value, now):
    try:
        if type(value) in (int, float) and math.isfinite(value):
            until = datetime.fromtimestamp(value, timezone.utc)
        elif isinstance(value, str):
            until = datetime.fromisoformat(value.replace('Z', '+00:00'))
        else: until = now+timedelta(minutes=1)
        if until.tzinfo is None or until <= now: until = now+timedelta(minutes=1)
        return until
    except (ValueError, TypeError, OverflowError, OSError): return now+timedelta(minutes=1)


class WebCommandProcessor:
    def __init__(self, repository, service, *, clock=time.time):
        self.repo, self.service, self.clock = repository, service, clock

    def tick(self):
        require_root_lock(self.service.data_root)
        now = datetime.fromtimestamp(self.clock(), timezone.utc)
        try: command = self.repo.pending(now)
        except RuntimeError as error:
            # Existing CLI workers remain compatible before explicit web migration.
            if str(error) == 'MIGRATION_REQUIRED': return
            raise
        if command is None: return
        try:
            if command['target_task'] is not None:
                task = self.repo.task(command['target_task'])
                expected = command['target_attempt']
                if task['attempt_no'] == expected:
                    self.service.tasks.retry(task['task_id'], now=now.isoformat())
                elif task['attempt_no'] != expected+1:
                    raise ValueError('RETRY_ATTEMPT_CHANGED')
                self.repo.link(command['id'], task['task_id'])
            else:
                if command['batch_id'] is None:
                    batch = self.service.prepare_batch(command['start_date'], command['end_date'], now=now)
                    self.repo.patch(command['id'], now=now, batch_id=batch['batch_id'])
                else:
                    batch = self.service.batch_store.load(command['batch_id'])
                # Persisted batch is authoritative across process restart; no changed DB re-plan.
                for day in batch['snapshot']['sessions']:
                    # Immediate manual post-close jobs are distinct from the
                    # scheduler's 20:00 trigger. The original task contract has
                    # minute precision and its earliest valid time is 16:01.
                    scheduled = datetime.fromisoformat(day+'T16:01:00+08:00')
                    if scheduled > now:
                        self.repo.patch(command['id'], now=now, status='WAITING_RETRY',
                            error_kind=None, retry_not_before=stamp(scheduled))
                        return
                    task = self.service.tasks.submit_bound(day, batch, scheduled_for=scheduled.isoformat(), now=now.isoformat())
                    self.repo.link(command['id'], task['task_id'])
            self.repo.patch(command['id'], now=now, status='SUBMITTED', error_kind=None, retry_not_before=None)
        except Exception as error:
            kind = getattr(error, 'kind', None)
            if kind not in ERRORS: kind = str(error) if str(error) in ERRORS else 'SCHEMA'
            if kind in RETRYABLE_TASK_ERRORS:
                deadline = getattr(error, 'retry_not_before', None) if isinstance(error, PreparationFailure) else None
                until = retry_deadline(deadline, now)
                self.repo.patch(command['id'], now=now, status='WAITING_RETRY', error_kind=kind, retry_not_before=stamp(until))
            else:
                self.repo.patch(command['id'], now=now, status='FAILED', error_kind=kind)
