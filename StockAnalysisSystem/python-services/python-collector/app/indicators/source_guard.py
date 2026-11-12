"""Native source write fence for short, explicit indicator verification/cutover.

This is not a scheduler pause flag: MySQL itself drains existing writers and
blocks new stock_daily writes, including administrative SQL. Always combine it
with the lifecycle's route-row lock and unresolved-delivery checks. Never hold
this guard while waiting for ingestion of pending work.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from app.events.event_factory import EventFactory
from app.kafka.serializer import KafkaJsonSerializer
from app.models.stock_daily import StockDailyRecord
from app.preparation.calendar import resolve_window
from app.preparation.contracts import TZ
from .capture import FIELDS, require_capture, source_scope
from .outbox import identity, integer
from .wire import _code, _loads, require_binding


def completed_cutoff(calendar, now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('Timezone-aware verification clock required')
    window = resolve_window('2023-01-01', now.astimezone(TZ).date().isoformat(), calendar, now)
    if window['empty']:
        raise ValueError('Completed source session required')
    return window['end']


class MySqlSourceGuard:
    def __init__(self, engine, calendar, *, clock=None, lock_wait_seconds=30):
        if engine.dialect.name != 'mysql':
            raise ValueError('Native MySQL source write fence required')
        self.engine = engine
        self.calendar = calendar
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lock_wait_seconds = integer(lock_wait_seconds, 1, 60)

    @contextmanager
    def hold(self, binding):
        require_binding(binding)
        cutoff = completed_cutoff(self.calendar, self.clock())
        with self.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
            require_capture(connection)
            connection.exec_driver_sql(f'SET SESSION lock_wait_timeout={self.lock_wait_seconds}')
            connection.exec_driver_sql('LOCK TABLES stock_daily READ')
            fence = None
            try:
                fence = _SourceFence(self.engine, connection, cutoff)
                yield fence
                fence.assert_held()
            finally:
                if fence is not None:
                    fence.active = False
                try:
                    connection.exec_driver_sql('UNLOCK TABLES')
                except Exception:
                    # Never return a possibly still-locked session to the pool.
                    connection.invalidate()
                    raise


class _SourceFence:
    def __init__(self, engine, connection, cutoff):
        self.engine = engine
        self.connection = connection
        self.connection_id = connection.exec_driver_sql('SELECT CONNECTION_ID()').scalar_one()
        self.cutoff = cutoff
        self.token = uuid4().hex
        self.active = True
        self.observed_at = datetime.now(timezone.utc)

    def assert_held(self):
        if (not self.active or self.connection.closed or self.connection.invalidated
                or self.connection.exec_driver_sql('SELECT CONNECTION_ID()').scalar_one() != self.connection_id):
            raise ValueError('Source write fence lost')

    def codes(self):
        self.assert_held()
        sql = text(f'SELECT DISTINCT d.ts_code FROM stock_daily d WHERE {source_scope("mysql", "d")} AND d.trade_date<=:end ORDER BY d.ts_code')
        with self.engine.connect() as connection:
            for row in connection.execute(sql, dict(end=self.cutoff)):
                self.assert_held()
                yield _code(row.ts_code)

    def rows(self, generation, code):
        identity(generation); _code(code); self.assert_held()
        columns = ','.join(f'd.`{field}`' for field in FIELDS)
        sql = text(f'SELECT {columns}, COALESCE((SELECT MAX(o.id) FROM indicator_outbox o WHERE o.ts_code=d.ts_code AND o.trade_date=d.trade_date),0) AS source_revision FROM stock_daily d WHERE {source_scope("mysql", "d")} AND d.ts_code=:code AND d.trade_date<=:end ORDER BY d.trade_date')
        with self.engine.connect() as connection:
            result = connection.execution_options(stream_results=True, yield_per=500).execute(sql, dict(code=code, end=self.cutoff))
            try:
                for index, row in enumerate(result.mappings()):
                    if index % 500 == 0:
                        self.assert_held()
                    values = {field: row[field] for field in FIELDS}
                    if type(values['trade_date']) is not date:
                        values['trade_date'] = date.fromisoformat(str(values['trade_date']))
                    event = EventFactory.create_daily_event(StockDailyRecord(**values, source='MYSQL_SNAPSHOT'),
                        trace_id='indicator-verification:' + self.token, ingest_time=self.observed_at)
                    yield dict(generation=generation, schemaVersion=2,
                        sourceRevision=integer(row['source_revision'], 0, 2**63-1),
                        event=_loads(KafkaJsonSerializer.serialize(event)))
            finally:
                result.close()
        self.assert_held()
