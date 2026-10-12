import importlib
import os
from pathlib import Path
import sys
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.orm import sessionmaker


def cls():
    try: return importlib.import_module('app.repositories.preparation_import_repository').PreparationImportRepository
    except ModuleNotFoundError: pytest.fail('preparation insert repository not implemented')


@contextmanager
def database():
    if os.getenv('PREPARATION_TEST_MYSQL')=='1':
        sys.path.insert(0,str(Path(__file__).resolve().parent))
        from preparation_store_support import database as temporary_store
        with temporary_store() as factory:
            conn=factory.kw['bind']
            conn.execute(text("CREATE TEMPORARY TABLE stock_daily_basic (ts_code VARCHAR(10),trade_date DATE,pe DECIMAL(20,6) NULL,source ENUM('TUSHARE') NOT NULL,UNIQUE(ts_code,trade_date))"))
            conn.execute(text("INSERT INTO stock_daily_basic VALUES ('000001.SZ','2026-09-28',NULL,'TUSHARE')"))
            conn.commit()
            # This connection supports connect() semantics for test read-back.
            class ReadEngine:
                @contextmanager
                def connect(self):
                    yield conn
                    conn.rollback()
            yield factory,ReadEngine()
        return
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stock_daily_basic (ts_code TEXT,trade_date TEXT,pe NUMERIC,source TEXT CHECK(source='TUSHARE'),UNIQUE(ts_code,trade_date))"))
        conn.execute(text("INSERT INTO stock_daily_basic VALUES ('000001.SZ','2026-09-28',NULL,'TUSHARE')"))
    try: yield sessionmaker(bind=engine),engine
    finally: engine.dispose()


def row(code='600000.SH',pe='10.000000',source='TUSHARE'):
    return dict(ts_code=code,trade_date='2026-09-28',pe=pe,source=source)


def test_insert_count_and_retry_are_actual_not_submitted():
    typ=cls()
    with database() as (factory,engine):
        repo=typ(factory)
        first=repo.insert_batch('stock_daily_basic',[row(),row('000001.SZ',None)],{'pe':'decimal(20,6)'})
        assert first==dict(inserted=1,already_equal=1,conflicts=[])
        second=repo.insert_batch('stock_daily_basic',[row()],{'pe':'decimal(20,6)'})
        assert second==dict(inserted=0,already_equal=1,conflicts=[])


def test_conflict_rolls_back_this_batch_without_overwriting_old_row():
    typ=cls()
    with database() as (factory,engine):
        result=typ(factory).insert_batch('stock_daily_basic',[row(),row('000001.SZ','999')],{'pe':'decimal(20,6)'})
        assert result['inserted']==0 and len(result['conflicts'])==1
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar()==1
            assert conn.execute(text('SELECT pe FROM stock_daily_basic')).scalar() is None


def test_nonduplicate_constraint_error_rolls_back_prior_candidate():
    typ=cls()
    with database() as (factory,engine):
        with pytest.raises(Exception):
            typ(factory).insert_batch('stock_daily_basic',[row(),row('600001.SH',source='BAD')],{'pe':'decimal(20,6)'})
        with engine.connect() as conn: assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar()==1


def test_owner_fence_fails_before_any_write():
    typ=cls()
    with database() as (factory,engine):
        def rejected(session): raise RuntimeError('OWNER_EXPIRED')
        with pytest.raises(RuntimeError,match='OWNER_EXPIRED'):
            typ(factory,owner_guard=rejected).insert_batch('stock_daily_basic',[row()],{'pe':'decimal(20,6)'})
        with engine.connect() as conn: assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar()==1


def test_duplicate_race_current_read_is_not_unconditional_ignore():
    typ=cls()
    with database() as (factory,engine):
        repo=typ(factory)
        read=repo._read_targets
        count=[0]
        def missing_once(session,table,rows):
            count[0]+=1
            return {} if count[0]==1 else read(session,table,rows)
        repo._read_targets=missing_once
        result=repo.insert_batch('stock_daily_basic',[row('000001.SZ',None)],{'pe':'decimal(20,6)'})
        assert result['already_equal']==1 and result['inserted']==0
        count[0]=0
        result=repo.insert_batch('stock_daily_basic',[row('000001.SZ','999')],{'pe':'decimal(20,6)'})
        assert len(result['conflicts'])==1


def test_batch_size_and_table_whitelist_checked_before_database():
    typ=cls()
    def forbidden(): pytest.fail('invalid candidate reached database')
    repo=typ(forbidden)
    with pytest.raises(ValueError): repo.insert_batch('stock_basic',[row()],{})
    with pytest.raises(ValueError): repo.insert_batch('stock_daily_basic',[row()]*501,{})
