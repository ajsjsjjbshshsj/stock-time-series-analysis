"""Only frozen targets may be inserted or read back; old rows survive races."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from copy import deepcopy
import pytest
from sqlalchemy import event, text
from test_preparation_import_repository import database, row
from app.preparation.importer import FileReceiptStore

TABLE = 'stock_daily_basic'
SCHEMA = dict(types={'pe': 'decimal(20,6)'})
KEY = ('600000.SH', '2026-09-28')


def repo(factory, guard=None):
    from app.repositories.gap_import_repository import GapImportRepository
    return GapImportRepository(factory, owner_guard=guard)


def test_only_allowed_missing_keys_are_inserted():
    with database() as (factory, engine):
        repository = repo(factory)
        with pytest.raises(ValueError):
            repository.insert_batch(TABLE, [row()], allowed_keys=[], schema=SCHEMA)
        result = repository.insert_batch(TABLE, [row()], allowed_keys=[KEY], schema=SCHEMA)
        assert result['inserted'] == 1 and result['already_present'] == 0
        verified = repository.verify_targets(TABLE, [row()], owned_insert_keys=[KEY], schema=SCHEMA)
        assert verified == dict(missing=[], conflicts=[])


def test_existing_old_row_never_updated():
    with database() as (factory, engine):
        repository = repo(factory)
        sql = []
        event.listen(engine, 'before_cursor_execute', lambda conn, cur, stmt, params, ctx, many: sql.append(stmt))
        repository.insert_batch(TABLE, [row()], allowed_keys=[KEY], schema=SCHEMA)
        repository.verify_targets(TABLE, [row()], owned_insert_keys=[KEY], schema=SCHEMA)
        assert all('where' in stmt.lower() for stmt in sql if stmt.lower().startswith('select'))
        assert not any(stmt.lower().startswith('update') for stmt in sql)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT pe FROM stock_daily_basic WHERE ts_code='000001.SZ'")).scalar() is None


def test_target_race_is_preserved_and_reported():
    with database() as (factory, engine):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO stock_daily_basic VALUES ('600000.SH','2026-09-28',999,'TUSHARE')"))
        result = repo(factory).insert_batch(TABLE, [row()], allowed_keys=[KEY], schema=SCHEMA)
        assert result['inserted'] == 0 and result['already_present'] == 1
        assert result['conflicts'] == [dict(table=TABLE, ts_code=KEY[0], trade_date=KEY[1])]
        with engine.connect() as conn:
            assert conn.execute(text("SELECT pe FROM stock_daily_basic WHERE ts_code='600000.SH'")).scalar() == 999


def invoke(repository, receipts):
    from app.preparation.gap_importer import import_gap_candidates
    return import_gap_candidates(repository, {TABLE: [row()]}, binding=dict(batch_sha256='a'*64,
        coverage_sha256='b'*64, request_sha256='c'*64), allowed_keys={TABLE: [KEY]},
        schema={TABLE: SCHEMA}, receipt_store=receipts, on_progress=lambda progress: None)


def test_commit_before_receipt_recovers_without_duplicate(tmp_path):
    class Interrupted(FileReceiptStore):
        def __init__(self, path):
            super().__init__(path)
            self.interrupted = False
        def save(self, state):
            if state['batches'] and not self.interrupted:
                self.interrupted = True
                raise RuntimeError('AFTER_COMMIT')
            super().save(state)
    with database() as (factory, engine):
        repository, receipts = repo(factory), Interrupted(tmp_path)
        with pytest.raises(RuntimeError, match='AFTER_COMMIT'):
            invoke(repository, receipts)
        result = invoke(repository, receipts)
        assert result['inserted'] == 0 and result['already_present'] == 1
        assert result['db_import_complete'] is True
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar() == 2


def test_owner_fence_uses_same_transaction():
    seen = []
    with database() as (factory, engine):
        def guard(session):
            seen.append(session.connection())
        event.listen(engine, 'before_cursor_execute', lambda conn, cur, stmt, params, ctx, many:
            pytest.fail('WRITE_OUTSIDE_FENCED_TRANSACTION') if stmt.startswith('INSERT') and conn not in seen else None)
        result = repo(factory, guard).insert_batch(TABLE, [row()], allowed_keys=[KEY], schema=SCHEMA)
        assert result['inserted'] == 1
        def deny(session):
            raise RuntimeError('OWNER_LOST')
        with pytest.raises(RuntimeError, match='OWNER'):
            repo(factory, deny).insert_batch(TABLE, [row('600001.SH')],
                allowed_keys=[('600001.SH', KEY[1])], schema=SCHEMA)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar() == 2


def test_constraint_failure_rolls_back_and_hides_db_details():
    with database() as (factory, engine):
        with pytest.raises(RuntimeError, match='GAP_DATABASE') as error:
            repo(factory).insert_batch(TABLE, [row(), row('600001.SH', source='SECRET')],
                allowed_keys=[KEY, ('600001.SH', KEY[1])], schema=SCHEMA)
        assert 'SECRET' not in str(error.value)
        assert error.value.__context__ is None
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar() == 1


def test_receipt_recovery_rechecks_only_owned_target_values(tmp_path):
    with database() as (factory, engine):
        repository, receipts = repo(factory), FileReceiptStore(tmp_path)
        assert invoke(repository, receipts)['inserted'] == 1
        with engine.begin() as conn:
            conn.execute(text("UPDATE stock_daily_basic SET pe=999 WHERE ts_code='600000.SH'"))
        result = invoke(repository, receipts)
        assert result['db_import_complete'] is False and len(result['conflicts']) == 1


def test_receipt_binding_tamper_rejected_before_sql(tmp_path):
    with database() as (factory, engine):
        repository, receipts = repo(factory), FileReceiptStore(tmp_path)
        invoke(repository, receipts)
        state = receipts.load()
        state['binding']['batch_sha256'] = 'd'*64
        receipts.save(state)
        with pytest.raises(ValueError, match='BINDING'):
            invoke(repository, receipts)


def test_unowned_recovery_target_conflict_cannot_be_hidden_by_receipt(tmp_path):
    with database() as (factory, engine):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO stock_daily_basic VALUES ('600000.SH','2026-09-28',999,'TUSHARE')"))
        repository, receipts = repo(factory), FileReceiptStore(tmp_path)
        assert invoke(repository, receipts)['db_import_complete'] is False
        state = receipts.load()
        for batch in state['batches'].values():
            batch['conflicts'] = []
        receipts.save(state)
        assert invoke(repository, receipts)['db_import_complete'] is False
