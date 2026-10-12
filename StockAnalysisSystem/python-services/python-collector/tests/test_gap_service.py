"""End-to-end gap queue contracts against isolated SQL and sealed raw sources."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd
import pytest
from sqlalchemy import text
from nightly_store_support import database, OWNER
from nightly_support import reference, calendar
from test_nightly_acquisition import Pacing, quote
from app.market_data.repair_contract import DAILY, BASIC, SUSPEND
from app.preparation.worker import PreparationWorker

NOW = datetime.fromisoformat('2026-10-12T10:00:00+08:00').timestamp()
DAY = '2026-09-28'


@contextmanager
def fixture(tmp_path, *, complete=False, basic_missing=False, empty=False, codes=None):
    from app.nightly.gap_store import GapBatchStore
    from app.repositories.gap_task_repository import GapTaskRepository
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    from app.repositories.gap_import_repository import GapImportRepository
    from app.repositories.source_evidence_repository import SourceEvidenceRepository
    from app.preparation.evidence import EvidenceStore
    from app.preparation.gap_acquisition import GapAcquirer
    from app.nightly.gap_service import GapUpdateService
    from app.nightly.universe import MarketUniverseProvider
    from app.nightly.reference import SnapshotReferenceWriter
    roster = [reference(), reference('600000.SH', exchange='SSE')]
    if codes:
        roster += [reference(code) for code in codes]
    calls = []
    class Client:
        def stock_basic(self, **params):
            from app.nightly.contracts import FIELDS
            return pd.DataFrame([row for row in roster if row['exchange'] == params['exchange']
                                 and row['list_status'] == params['list_status']], columns=FIELDS)
        def daily(self, **params):
            calls.append(('daily', params))
            candidates = [] if empty else [dict(quote(code), trade_date='20260928') for code in
                ([params['ts_code']] if 'ts_code' in params else [r['ts_code'] for r in roster])]
            return pd.DataFrame(candidates, columns=DAILY)
        def daily_basic(self, **params):
            calls.append(('daily_basic', params))
            codes_ = [params['ts_code']] if 'ts_code' in params else [r['ts_code'] for r in roster]
            return pd.DataFrame([dict(ts_code=code, trade_date='20260928', turnover_rate=1., pe=None,
                pe_ttm=None, pb=1., ps=1., total_mv=1000.) for code in codes_], columns=BASIC)
        def suspend_d(self, **params):
            calls.append(('suspend_d', params))
            return pd.DataFrame([], columns=SUSPEND)
    with database() as factory:
        with factory.begin() as session:
            for table, fields in (('stock_daily', DAILY), ('stock_daily_basic', BASIC)):
                columns = ','.join(f'{field} '+('TEXT' if field in ('ts_code','trade_date') else 'DECIMAL(20,6)') for field in fields)
                if table == 'stock_daily_basic':
                    columns += ',source TEXT'
                session.execute(text(f'CREATE TABLE {table} ({columns}, UNIQUE(ts_code,trade_date))'))
            session.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date DATE)'))
            for code in [r['ts_code'] for r in roster]:
                if complete or basic_missing or code == '600000.SH':
                    values = dict(quote(code), trade_date=DAY)
                    session.execute(text('INSERT INTO stock_daily ('+','.join(values)+') VALUES ('+','.join(':'+f for f in values)+')'), values)
                if complete or code == '600000.SH':
                    session.execute(text('INSERT INTO stock_daily_basic (ts_code,trade_date,pe) VALUES (:code,:day,NULL)'), dict(code=code, day=DAY))
        provider = MarketUniverseProvider(lambda: Client(), lambda root: Pacing(), clock=lambda: NOW, schema_version=2)
        provider.pinned_reference = provider.acquire_reference(tmp_path/'reference')
        store = GapBatchStore(tmp_path)
        tasks = GapTaskRepository(factory, batch_store=store)
        source = EvidenceStore(tmp_path, SourceEvidenceRepository(factory))
        acq = GapAcquirer(lambda: Client(), lambda root: Pacing(), source_store=source, clock=lambda: NOW)
        service = GapUpdateService(tasks, GapCoverageRepository(factory), GapImportRepository(factory),
            SimpleNamespace(get=lambda start, end, now, acquire=False: calendar()), provider, acq,
            SnapshotReferenceWriter(factory), store, tmp_path, clock=lambda: NOW)
        lock = PreparationWorker(tasks, service).maintenance_lock()
        yield factory, tasks, service, store, calls, lock


def run(tasks, service, batch):
    now = datetime.fromtimestamp(service.clock(), timezone.utc)
    task = tasks.submit_bound(DAY, batch, scheduled_for=DAY+'T20:00:00+08:00', now=now.isoformat())
    from datetime import timedelta
    claimed = tasks.claim(OWNER, now=now.isoformat(), lease_until=(now+timedelta(seconds=120)).isoformat())
    assert claimed['task_id'] == task['task_id']
    return service.execute(claimed, owner_token=OWNER['token'])


def test_complete_db_worker_does_not_load_old_price_sources(tmp_path):
    with fixture(tmp_path, complete=True) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert result['status'] == 'SUCCEEDED' and calls == []
        assert result['result']['inserted'] == 0
        assert result['result']['verification_mode'] == 'missing-only'
        assert result['result']['existing_data_revalidated'] is False
        assert result['result']['model_ready'] is False


def test_only_basic_missing_worker_skips_daily(tmp_path):
    with fixture(tmp_path, basic_missing=True) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert result['status'] == 'SUCCEEDED'
        assert [name for name, params in calls] == ['daily_basic']
        assert result['result']['inserted'] == 1


def test_new_quotes_freeze_basic_followups(tmp_path):
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert result['status'] == 'SUCCEEDED'
        assert [name for name, params in calls] == ['daily', 'daily_basic']
        assert result['result']['inserted'] == 2
        assert list(Path(batch['path']).glob('followups/*.json'))


def test_empty_unknown_quotes_remain_partial(tmp_path):
    with fixture(tmp_path, empty=True) as (factory, tasks, service, store, calls, lock), lock:
        result = run(tasks, service, service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc)))
        assert result['status'] == 'PARTIAL'
        assert result['result']['unresolved_keys'] == 1
        assert result['result']['data_ready'] is False
        assert not any(name == 'daily_basic' for name, params in calls)


def test_loss_of_owner_prevents_import_and_publish(tmp_path):
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        original = service.acquirer.collect
        def collect(*args, **kwargs):
            value = original(*args, **kwargs)
            with factory.begin() as session:
                session.execute(text("UPDATE market_update_run SET owner_token=:token WHERE status='RUNNING'"), dict(token='2'*32))
            return value
        service.acquirer.collect = collect
        with pytest.raises(RuntimeError, match='OWNER'):
            run(tasks, service, batch)
        with factory() as session:
            assert session.execute(text("SELECT COUNT(*) FROM stock_daily WHERE ts_code='000001.SZ'")).scalar() == 0


def test_same_day_new_snapshot_does_not_reuse_stale_success(tmp_path):
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock), lock:
        first = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, first)
        second = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        assert first['full_sha256'] != second['full_sha256']
        task = tasks.submit_bound(DAY, second, scheduled_for=DAY+'T20:00:00+08:00',
            now=datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        assert task['task_id'] != result['task_id'] and task['status'] == 'QUEUED'


def test_batch_full_hash_collision_rejected(tmp_path, monkeypatch):
    from app.nightly import gap_store
    from gap_support import coverage_fixture
    snapshot = coverage_fixture([DAY])
    worker = PreparationWorker(None, SimpleNamespace(data_root=tmp_path))
    with worker.maintenance_lock():
        store = gap_store.GapBatchStore(tmp_path)
        real_identity = gap_store.identity
        monkeypatch.setattr(gap_store, 'identity', lambda body: '1'*16+'a'*48
            if 'strategy' in body and 'snapshot' in body else real_identity(body))
        store.create_or_load(snapshot, [])
        monkeypatch.setattr(gap_store, 'identity', lambda body: '1'*16+'b'*48
            if 'strategy' in body and 'snapshot' in body else real_identity(body))
        with pytest.raises(ValueError, match='COLLISION'):
            store.create_or_load(snapshot, [])


def test_budget_yield_preserves_fragment_cursor(tmp_path):
    codes = [f'{i:06d}.SZ' for i in range(2, 30)]
    with fixture(tmp_path, codes=codes) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert result['status'] == 'WAITING_RETRY' and result['error_kind'] is None
        assert len(calls) == 25
        assert result['progress']['completed_requests'] > 0
        assert datetime.fromisoformat(result['retry_not_before']).timestamp() >= NOW+1


def test_quota_then_owner_loss_does_not_spin(tmp_path):
    from app.preparation.service import PreparationFailure
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        def denied(*args, **kwargs):
            calls.append(('quota', {}))
            with factory.begin() as session:
                session.execute(text("UPDATE market_update_run SET owner_token=:token WHERE status='RUNNING'"), dict(token='2'*32))
            raise PreparationFailure('MINUTE_QUOTA', retry_not_before=NOW+61)
        service.acquirer.collect = denied
        with pytest.raises(RuntimeError, match='OWNER'):
            run(tasks, service, batch)
        assert len(calls) == 1


def test_legacy_getter_new_claim_and_cross_version_retry(tmp_path):
    from app.repositories.market_update_repository import MarketUpdateRepository
    with fixture(tmp_path, complete=True) as (factory, tasks, service, store, calls, lock), lock:
        old = MarketUpdateRepository(factory)
        legacy = old.submit(DAY, scheduled_for=DAY+'T20:00:00+08:00', now=datetime.fromtimestamp(NOW, timezone.utc).isoformat())
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert tasks.get(legacy['task_id']) == legacy
        assert old.get(legacy['task_id'])['status'] == 'QUEUED'
        with pytest.raises(ValueError, match='VERSION'):
            tasks.retry(legacy['task_id'], now=datetime.fromtimestamp(NOW, timezone.utc).isoformat())


def test_reference_pointer_change_rejected_on_recovery(tmp_path):
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        path = tmp_path/'reference'/'SSE_L.parquet'
        path.write_bytes(b'changed')
        result = run(tasks, service, batch)
        assert result['status'] == 'FAILED' and calls == []


def test_metadata_decimal_precision_is_canonical(tmp_path):
    with fixture(tmp_path) as (factory, tasks, service, store, calls, lock):
        assert service.coverage.schema('stock_daily')['types']['close'] == 'decimal(20,6)'


def test_dry_run_uses_local_reference_and_no_files_or_writes(tmp_path):
    with fixture(tmp_path, complete=True) as (factory, tasks, service, store, calls, lock):
        before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*'))
        result = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc),
            acquire_reference=False, persist=False)
        assert result['planned_missing_keys'] == 0 and result['request_segments'] == 0
        assert calls == []
        assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob('*')) == before
        assert tasks.earliest_date() is None


def test_checkpointed_raw_source_tamper_rejected_before_more_requests(tmp_path):
    codes = [f'{i:06d}.SZ' for i in range(2, 30)]
    with fixture(tmp_path, codes=codes) as (factory, tasks, service, store, calls, lock), lock:
        batch = service.prepare_batch(DAY, DAY, now=datetime.fromtimestamp(NOW, timezone.utc))
        result = run(tasks, service, batch)
        assert result['status'] == 'WAITING_RETRY'
        path = next((Path(batch['path'])/'sources').glob('*/shards/*.parquet'))
        path.write_bytes(b'tampered')
        service.clock = lambda: NOW+2
        from datetime import timedelta
        now = datetime.fromtimestamp(NOW+2, timezone.utc)
        claimed = tasks.claim(OWNER, now=now.isoformat(), lease_until=(now+timedelta(seconds=120)).isoformat())
        count = len(calls)
        result = service.execute(claimed, owner_token=OWNER['token'])
        assert result['status'] == 'FAILED'
        assert len(calls) == count
