"""Real SQL transactions exercise insert-only accounting and rollback."""
from contextlib import contextmanager
import importlib

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session


@pytest.fixture
def repository():
    cls=importlib.import_module('app.repositories.market_repair_repository').MarketRepairRepository
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_daily_basic (ts_code TEXT,trade_date TEXT,pe NUMERIC NULL,source TEXT, UNIQUE(ts_code,trade_date),CHECK (source="TUSHARE"))'))
        conn.execute(text("INSERT INTO stock_daily_basic VALUES ('000001.SZ','2023-10-09',NULL,'TUSHARE')"))
    return cls(lambda:Session(engine)),engine


def method(repo,name):
    result=getattr(repo,name,None)
    assert callable(result),f'{name} is not implemented'
    return result


def row(code='000002.SZ',pe='10.000000',source='TUSHARE'):
    return dict(ts_code=code,trade_date='2023-10-09',pe=pe,source=source)


def test_insert_count_is_actual_not_submitted_and_existing_null_unchanged(repository):
    repo,engine=repository
    inserted=method(repo,'_insert_batch')('stock_daily_basic',[row(),row('000001.SZ',None)],{'pe':'decimal(20,6)'})
    assert inserted==dict(inserted=1,already_equal=1,conflicts=[])
    with engine.connect() as conn:
        assert conn.execute(text("SELECT pe FROM stock_daily_basic WHERE ts_code='000001.SZ'")).scalar() is None


def test_existing_key_conflict_never_overwrites_and_rolls_back_batch(repository):
    repo,engine=repository
    result=method(repo,'_insert_batch')('stock_daily_basic',[row(),row('000001.SZ','20')],{'pe':'decimal(20,6)'})
    assert result['inserted']==0 and len(result['conflicts'])==1
    with engine.connect() as conn: assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar()==1


def test_constraint_failure_rolls_back_all_prior_batch_inserts(repository):
    repo,engine=repository
    insert=method(repo,'_insert_batch')
    with pytest.raises(Exception): insert('stock_daily_basic',[row(),row('000003.SZ',source='INVALID')],{'pe':'decimal(20,6)'})
    with engine.connect() as conn: assert conn.execute(text('SELECT COUNT(*) FROM stock_daily_basic')).scalar()==1


def test_retry_after_commit_counts_present_rows_without_reinsertion(repository):
    repo,engine=repository
    insert=method(repo,'_insert_batch'); first=insert('stock_daily_basic',[row()],{'pe':'decimal(20,6)'})
    second=insert('stock_daily_basic',[row()],{'pe':'decimal(20,6)'})
    assert first['inserted']==1 and second['inserted']==0 and second['already_equal']==1


def test_duplicate_race_reads_back_same_or_conflicting_row(repository):
    repo,engine=repository
    # Suppress the first pre-read to expose the real unique-key/savepoint branch.
    original=getattr(repo,'_get_row',None)
    assert original is not None,'read-back primitive is not implemented'
    calls=[0]
    def miss_once(session,table,candidate):
        calls[0]+=1
        return None if calls[0]==1 else original(session,table,candidate)
    repo._get_row=miss_once
    result=method(repo,'_insert_batch')('stock_daily_basic',[row('000001.SZ',None)],{'pe':'decimal(20,6)'})
    assert result['inserted']==0 and result['already_equal']==1
    calls[0]=0
    result=repo._insert_batch('stock_daily_basic',[row('000001.SZ','99')],{'pe':'decimal(20,6)'})
    assert result['inserted']==0 and len(result['conflicts'])==1


def test_invalid_package_refused_before_session_created(tmp_path):
    cls=importlib.import_module('app.repositories.market_repair_repository').MarketRepairRepository
    def forbidden(): pytest.fail('DB connected before source verification')
    repo=cls(forbidden)
    with pytest.raises(ValueError): method(repo,'import_snapshot')(tmp_path/'bad',tmp_path/'ledger')


@pytest.mark.parametrize('change',['value','timestamp','schema','deleted'])
def test_original_table_rows_and_schema_are_preserved(repository,change):
    repo,_=repository
    original=dict(schema={'stock_daily':{'columns':['id','close','created_at']},'stock_daily_basic':{}},
        stock_daily=[dict(ts_code='000001.SZ',trade_date='2023-10-09',id=1,close='12.000000',created_at='old')],stock_daily_basic=[])
    import copy
    current=copy.deepcopy(original)
    if change=='value': current['stock_daily'][0]['close']='13.000000'
    if change=='timestamp': current['stock_daily'][0]['created_at']='new'
    if change=='schema': current['schema']['stock_daily']['columns'].append('new')
    if change=='deleted': current['stock_daily']=[]
    with pytest.raises(ValueError): method(repo,'_assert_original')(original,current)
