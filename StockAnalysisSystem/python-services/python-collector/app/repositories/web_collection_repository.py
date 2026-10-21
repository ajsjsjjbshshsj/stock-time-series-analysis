"""Durable administrator commands. HTTP only writes this inbox, never collects."""
from datetime import datetime, timedelta, time, timezone
import json
import re
import uuid

from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, UniqueConstraint,
                        Index, select, insert, update, func, text)
from sqlalchemy.exc import IntegrityError
from app.preparation.contracts import TZ, identity
from app.nightly.contracts import market_day
from ._preparation_sql import transaction, stamp, utc

metadata = MetaData()
commands = Table('market_collection_request', metadata,
    Column('id', String(32), primary_key=True), Column('actor_id', String(32), nullable=False),
    Column('request_key', String(32), nullable=False), Column('payload_sha256', String(64), nullable=False),
    Column('start_date', String(10), nullable=False), Column('end_date', String(10), nullable=False),
    Column('target_task', String(32)), Column('target_attempt', Integer),
    Column('status', String(24), nullable=False), Column('batch_id', String(40)),
    Column('error_kind', String(48)), Column('retry_not_before', String(32)),
    Column('created_at', String(32), nullable=False), Column('updated_at', String(32), nullable=False),
    UniqueConstraint('actor_id', 'request_key', name='uq_collection_request_key'))
Index('ix_collection_request_pending', commands.c.status, commands.c.created_at)
links = Table('market_collection_request_day', metadata,
    Column('request_id', String(32), primary_key=True),
    Column('task_id', String(32), primary_key=True))


def migrate(engine):
    """Explicit CLI migration only; never called by imports, worker or HTTP."""
    metadata.create_all(engine)


def identifier(value):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('INVALID_ID')
    return value


def date_range(start, end, now):
    first, last = market_day(start), market_day(end)
    local = now.astimezone(TZ)
    cutoff = local.date() if local.time() > time(16) else local.date()-timedelta(days=1)
    if first > last or last > cutoff:
        raise ValueError('INVALID_RANGE')
    return first, last


def page(limit, offset):
    if type(limit) is not int or not 1 <= limit <= 50 or type(offset) is not int or not 0 <= offset <= 100000:
        raise ValueError('INVALID_PAGE')


class _ConcurrentInsert(Exception):
    pass


class CollectionCommands:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def _get(self, session, id):
        row = session.execute(select(commands).where(commands.c.id == identifier(id))).mappings().first()
        if row is None: raise LookupError('REQUEST_NOT_FOUND')
        result = dict(row)
        result['task_count'] = session.execute(select(func.count()).select_from(links).where(links.c.request_id == id)).scalar_one()
        return result

    def get(self, id):
        with transaction(self.session_factory) as session:
            return self._get(session, id)

    def submit(self, actor, key, start, end, *, now=None, target=None):
        identifier(actor); identifier(key)
        now = now or datetime.now(timezone.utc)
        date_range(start, end, now)
        if target is not None: identifier(target)
        digest = identity(dict(start=start, end=end, target=target))
        try:
            return self._submit(actor, key, start, end, now, target, digest)
        except _ConcurrentInsert:
            # MySQL REPEATABLE READ: the first miss pinned a snapshot which
            # cannot see the concurrent winner. Leave it, then read afresh.
            with transaction(self.session_factory) as session:
                existing = self._previous(session, actor, key, digest)
                if existing is None: raise RuntimeError('STORAGE_UNAVAILABLE')
                return existing

    def _previous(self, session, actor, key, digest):
        row = session.execute(select(commands.c.id, commands.c.payload_sha256).where(
            commands.c.actor_id == actor, commands.c.request_key == key)).first()
        if row is not None:
            if row.payload_sha256 != digest: raise ValueError('IDEMPOTENCY_CONFLICT')
            return self._get(session, row.id)

    def _submit(self, actor, key, start, end, now, target, digest):
        with transaction(self.session_factory) as session:
            existing = self._previous(session, actor, key, digest)
            if existing is not None: return existing
            attempt = None
            if target is not None:
                day = self._task(session, target)
                if day['status'] not in ('FAILED', 'PARTIAL'):
                    raise ValueError('TASK_NOT_RETRYABLE')
                if start != day['trade_date'] or end != start:
                    raise ValueError('RETRY_DATE_CHANGED')
                attempt = day['attempt_no']
            id = uuid.uuid4().hex
            try:
                with session.begin_nested():
                    session.execute(insert(commands).values(id=id, actor_id=actor, request_key=key,
                        payload_sha256=digest, start_date=start, end_date=end, target_task=target,
                        target_attempt=attempt, status='QUEUED', created_at=stamp(now), updated_at=stamp(now)))
            except IntegrityError:
                raise _ConcurrentInsert() from None
            return self._get(session, id)

    def pending(self, now):
        with transaction(self.session_factory) as session:
            row = session.execute(select(commands.c.id).where(
                commands.c.status.in_(['QUEUED', 'PLANNING', 'WAITING_RETRY']),
                (commands.c.retry_not_before.is_(None)) | (commands.c.retry_not_before <= stamp(now)))
                .order_by(commands.c.created_at, commands.c.id).limit(1)).first()
            if row is None: return None
            session.execute(update(commands).where(commands.c.id == row.id).values(status='PLANNING', updated_at=stamp(now)))
            return self._get(session, row.id)

    def patch(self, id, *, now, **values):
        if not values or not set(values) <= {'status', 'batch_id', 'error_kind', 'retry_not_before'}:
            raise ValueError('INVALID_PATCH')
        with transaction(self.session_factory) as session:
            session.execute(update(commands).where(commands.c.id == identifier(id)).values(**values, updated_at=stamp(now)))

    def link(self, request_id, task_id):
        identifier(request_id); identifier(task_id)
        with transaction(self.session_factory) as session:
            present = session.execute(select(links).where(links.c.request_id == request_id, links.c.task_id == task_id)).first()
            if present is None: session.execute(insert(links).values(request_id=request_id, task_id=task_id))

    @staticmethod
    def _task(session, id):
        row = session.execute(text("SELECT task_id,trade_date,status,phase,attempt_no,progress_json,result_json,error_kind,retry_not_before "
            "FROM market_update_run WHERE task_id=:id AND market_scope='SH_SZ_A' AND requirements_version LIKE 'market-gap-v1:%'"),
            dict(id=identifier(id))).mappings().first()
        if row is None: raise LookupError('TASK_NOT_FOUND')
        result = dict(row, trade_date=str(row['trade_date']))
        for name in ('progress', 'result'):
            raw = json.loads(result.pop(name+'_json') or '{}')
            # Numeric counters only, never raw source rows, paths, ownership or provider messages.
            result[name] = {k: v for k, v in raw.items() if k in {
                'completed_requests', 'total_requests', 'inserted', 'already_present',
                'api_requests', 'cache_hits', 'missing_daily', 'missing_basic'} and type(v) is int and v >= 0}
        result['retry_not_before'] = utc(result['retry_not_before'])
        return result

    def task(self, id):
        with transaction(self.session_factory) as session: return self._task(session, id)

    def list_commands(self, limit=20, offset=0):
        page(limit, offset)
        with transaction(self.session_factory) as session:
            ids = session.execute(select(commands.c.id).order_by(commands.c.created_at.desc(), commands.c.id.desc()).limit(limit).offset(offset)).scalars().all()
            items = [self._get(session, id) for id in ids]
            return dict(items=[{k: v for k, v in item.items() if k not in {'actor_id', 'request_key', 'payload_sha256', 'target_attempt'}} for item in items],
                total=session.execute(select(func.count()).select_from(commands)).scalar_one())

    def list_tasks(self, limit=20, offset=0, request_id=None):
        page(limit, offset)
        where = "market_scope='SH_SZ_A' AND requirements_version LIKE 'market-gap-v1:%'"
        params = dict(limit=limit, offset=offset)
        if request_id is not None:
            self.get(request_id)
            where += ' AND task_id IN (SELECT task_id FROM market_collection_request_day WHERE request_id=:request)'
            params['request'] = identifier(request_id)
        with transaction(self.session_factory) as session:
            ids = session.execute(text('SELECT task_id FROM market_update_run WHERE '+where+
                ' ORDER BY trade_date DESC,created_at DESC,task_id DESC LIMIT :limit OFFSET :offset'), params).scalars().all()
            total = session.execute(text('SELECT COUNT(*) FROM market_update_run WHERE '+where), params).scalar_one()
            return dict(items=[self._task(session, id) for id in ids], total=total)

    def summary(self):
        with transaction(self.session_factory) as session:
            requests = dict(session.execute(select(commands.c.status, func.count()).group_by(commands.c.status)).all())
            tasks = dict(session.execute(text("SELECT status,COUNT(*) FROM market_update_run WHERE market_scope='SH_SZ_A' "
                "AND requirements_version LIKE 'market-gap-v1:%' GROUP BY status")).all())
            return dict(requests=requests, tasks=tasks)

    def preview(self, table, limit=20):
        columns = {
            'stock_basic': 'ts_code,symbol,name,industry,list_date',
            'stock_daily': 'ts_code,trade_date,open,high,low,close,vol,amount',
            'stock_daily_basic': 'ts_code,trade_date,turnover_rate,pe,pe_ttm,pb,ps,total_mv',
        }
        if table not in columns: raise ValueError('INVALID_TABLE')
        page(limit, 0)
        order = 'ts_code' if table == 'stock_basic' else 'trade_date DESC,ts_code'
        with transaction(self.session_factory) as session:
            rows = session.execute(text('SELECT '+columns[table]+' FROM '+table+
                " WHERE (ts_code LIKE '0%.SZ' OR ts_code LIKE '30%.SZ' OR ts_code LIKE '60%.SH' OR ts_code LIKE '68%.SH')"
                ' ORDER BY '+order+' LIMIT :limit'), dict(limit=limit)).mappings().all()
            return dict(items=[dict(row) for row in rows], table=table)
