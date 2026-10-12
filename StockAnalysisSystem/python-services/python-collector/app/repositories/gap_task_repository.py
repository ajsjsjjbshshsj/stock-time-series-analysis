"""Gap-family queue over the existing market task table; legacy jobs stay read-only."""
from sqlalchemy import text
from app.repositories.market_update_repository import MarketUpdateRepository, _task
from app.repositories._preparation_sql import transaction, stamp, utc
from app.nightly.contracts import GAP_VERSION, market_day, schema_for_version


class GapTaskRepository:
    def __init__(self, session_factory, *, batch_store=None, prepare_batch=None):
        self.session_factory, self.batch_store = session_factory, batch_store
        self.prepare_batch = prepare_batch
        self.reader = MarketUpdateRepository(session_factory)

    def get(self, task_id):
        return self.reader.get(task_id)

    def _repo(self, version):
        schema_for_version(version)
        if not version.startswith(GAP_VERSION+':'):
            raise ValueError('GAP_TASK_VERSION_MISMATCH')
        if self.batch_store is None:
            raise ValueError('GAP_BATCH_STORE_REQUIRED')
        self.batch_store.check_version(version)
        return MarketUpdateRepository(self.session_factory, requirements_version=version)

    def submit_bound(self, day, batch, *, scheduled_for, now):
        stored = self.batch_store.load(batch['batch_id'])
        if stored != batch or day not in stored['snapshot']['sessions']:
            raise ValueError('GAP_TASK_BATCH_BINDING_CHANGED')
        return self._repo(batch['batch_id']).submit(day, scheduled_for=scheduled_for, now=now)

    def submit(self, day, *, scheduled_for, now):
        if self.prepare_batch is None:
            raise ValueError('GAP_PREPARATION_REQUIRED')
        from datetime import datetime
        batch = self.prepare_batch(day, day, now=datetime.fromisoformat(now.replace('Z', '+00:00')))
        return self.submit_bound(day, batch, scheduled_for=scheduled_for, now=now)

    def _selected(self, suffix='', params=None):
        with transaction(self.session_factory) as session:
            return [dict(row) for row in session.execute(text(
                "SELECT * FROM market_update_run WHERE market_scope='SH_SZ_A' AND requirements_version LIKE 'market-gap-v1:%' "+suffix), params or {}).mappings().all()]

    def claim(self, owner, *, now, lease_until):
        rows = self._selected("AND status IN ('QUEUED','WAITING_RETRY') AND not_before<=:now "
            'AND (retry_not_before IS NULL OR retry_not_before<=:now) ORDER BY trade_date,created_at,task_id LIMIT 1', dict(now=stamp(now)))
        if not rows:return None
        return self._repo(rows[0]['requirements_version']).claim(owner, now=now, lease_until=lease_until)

    def fence(self, task_id, owner_token, *, now, session=None):
        task = self.reader._get(session, task_id) if session is not None else self.get(task_id)
        return self._repo(task['requirements_version']).fence(task_id, owner_token, now=now, session=session)

    def bind_universe(self, task_id, owner_token, snapshot, *, now):
        return self._repo(self.get(task_id)['requirements_version']).bind_universe(task_id, owner_token, snapshot, now=now)

    def heartbeat(self, task_id, owner_token, *, now, lease_until):
        return self._repo(self.get(task_id)['requirements_version']).heartbeat(task_id, owner_token, now=now, lease_until=lease_until)

    def update(self, task_id, owner_token, patch):
        return self._repo(self.get(task_id)['requirements_version']).update(task_id, owner_token, patch)

    def retry(self, task_id, *, now):
        return self._repo(self.get(task_id)['requirements_version']).retry(task_id, now=now)

    def expired(self, *, now):
        return [_task(row) for row in self._selected("AND status='RUNNING' AND lease_until<=:now", dict(now=stamp(now)))]

    def recover(self, task_id, old_owner_token, *, now):
        return self._repo(self.get(task_id)['requirements_version']).recover(task_id, old_owner_token, now=now)

    def earliest_date(self):
        rows = self._selected('ORDER BY trade_date LIMIT 1')
        return str(rows[0]['trade_date']) if rows else None

    def list_runs(self, start, end):
        if market_day(start) > market_day(end):raise ValueError('GAP_RANGE_INVALID')
        return [dict(task_id=row['task_id'], trade_date=str(row['trade_date']), status=row['status'],
            error_kind=row['error_kind'], requirements_version=row['requirements_version'],
            created_at=utc(row['created_at']), not_before=utc(row['not_before']), retry_not_before=utc(row['retry_not_before']))
            for row in self._selected('AND trade_date BETWEEN :start AND :end ORDER BY trade_date,created_at,task_id', dict(start=start, end=end))]

    def next_retry(self):
        rows = self._selected("AND status IN ('QUEUED','WAITING_RETRY')")
        return min((utc(max(row['not_before'], row['retry_not_before'] or row['not_before'])) for row in rows), default=None)
