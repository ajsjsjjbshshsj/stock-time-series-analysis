"""Reject full-value scans, unbounded pages and out-of-period coverage."""
from contextlib import contextmanager
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from gap_support import coverage_fixture


@contextmanager
def database(keys):
    engine = create_engine('sqlite://')
    factory = sessionmaker(bind=engine)
    with engine.begin() as conn:
        for table in ('stock_daily', 'stock_daily_basic'):
            conn.execute(text(f'CREATE TABLE {table} (ts_code TEXT, trade_date DATE, close DECIMAL(12,4), PRIMARY KEY(ts_code,trade_date))'))
            for code, day in keys:
                conn.execute(text(f'INSERT INTO {table} VALUES (:code,:day,12.3456)'), dict(code=code, day=day))
    try:
        yield factory, engine
    finally:
        engine.dispose()


def test_complete_coverage_uses_keys_not_old_values():
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    days = ['2026-09-25', '2026-09-28', '2026-09-29']
    keys = [('000001.SZ', day) for day in days]
    with database(keys) as (factory, engine):
        statements = []
        event.listen(engine, 'before_cursor_execute', lambda conn, cur, sql, params, ctx, many: statements.append(sql))
        repo = GapCoverageRepository(factory)
        value = coverage_fixture(days, daily_keys=repo.iter_keys('stock_daily', ['000001.SZ'], days[0], days[-1]),
            basic_keys=repo.iter_keys('stock_daily_basic', ['000001.SZ'], days[0], days[-1]))
        assert value['existing_counts'] == {'stock_daily': 3, 'stock_daily_basic': 3}
        assert value['daily_bits'] == {'000001.SZ': '7'}
        assert all('close' not in sql.lower() and 'select *' not in sql.lower() for sql in statements)


def test_stream_pages_without_full_materialization():
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    keys = [('000001.SZ', f'2026-09-{day}') for day in range(21, 26)]
    with database(keys) as (factory, engine):
        statements = []
        event.listen(engine, 'before_cursor_execute', lambda conn, cur, sql, params, ctx, many: statements.append(sql))
        stream = GapCoverageRepository(factory).iter_keys('stock_daily', ['000001.SZ'], '2026-09-21', '2026-09-25', page_size=2)
        assert statements == []
        assert next(stream) == ('000001.SZ', '2026-09-21')
        assert len(statements) == 1 and 'limit' in statements[0].lower()
        assert list(stream) == keys[1:]
        assert 3 <= len(statements) <= 4


def test_ipo_delist_and_independent_code_periods():
    days = ['2025-02-14', '2025-02-17', '2025-02-18']
    codes = ('300114.SZ', '302132.SZ', '000001.SZ')
    keys = [(code, day) for code in codes for day in days]
    snapshot = coverage_fixture(days, codes=codes, daily_keys=keys, basic_keys=keys,
        changes={'000001.SZ': dict(list_date='20250217', list_status='D', delist_date='20250218')})
    assert snapshot['daily_bits'] == {'000001.SZ': '2', '300114.SZ': '1', '302132.SZ': '6'}
    assert snapshot['existing_counts'] == {'stock_daily': 4, 'stock_daily_basic': 4}


def test_outside_scope_rows_are_not_deleted():
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    keys = [('000001.SZ', '2026-09-25'), ('430047.BJ', '2026-09-25'), ('000001.SZ', '2022-12-30')]
    with database(keys) as (factory, engine):
        repo = GapCoverageRepository(factory)
        assert list(repo.iter_keys('stock_daily', ['000001.SZ'], '2026-09-25', '2026-09-25')) == [keys[0]]
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_daily')).scalar_one() == 3
        with pytest.raises(ValueError):
            list(repo.iter_keys('stock_features', ['000001.SZ'], '2026-09-25', '2026-09-25'))


def test_calendar_holes_and_duplicate_keys_rejected():
    from app.preparation.gap_plan import make_coverage_snapshot
    from nightly_support import reference
    with pytest.raises(ValueError, match='CALENDAR'):
        make_coverage_snapshot([reference()], dict(source='tushare.trade_cal', rows=[]),
            '2026-09-25', '2026-09-25', [], [], reference_sha256='a'*64,
            acquired_at='2026-10-12T10:00:00+08:00')
    with pytest.raises(ValueError, match='DUPLICATE'):
        coverage_fixture(['2026-09-25'], daily_keys=[('000001.SZ', '2026-09-25')]*2)


def test_schema_reads_metadata_only():
    from app.repositories.gap_coverage_repository import GapCoverageRepository
    with database([]) as (factory, engine):
        schema = GapCoverageRepository(factory).schema('stock_daily')
        assert schema['columns'] == ['ts_code', 'trade_date', 'close']
        assert ['ts_code', 'trade_date'] in schema['unique_keys']
