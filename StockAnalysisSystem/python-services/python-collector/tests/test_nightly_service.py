import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from nightly_support import reference
from app.nightly.contracts import freeze_market
from app.preparation.service import PreparationFailure

DAY='2026-09-28'


def test_snapshot_reference_writer_does_not_overwrite_existing_info_or_accept_bj(tmp_path):
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    module=importlib.import_module('app.nightly.reference')
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
        conn.execute(text("INSERT INTO stock_basic VALUES ('000001.SZ','000001','保留名称','深圳','银行','1991-04-03')"))
    snapshot=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE',list_date='19991110')],'a'*64,DAY+'T20:00:00+08:00')
    writer=module.SnapshotReferenceWriter(sessionmaker(bind=engine))
    try:
        writer.ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lambda session:None)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT name FROM stock_basic WHERE ts_code='000001.SZ'")).scalar()=='保留名称'
            assert str(conn.execute(text("SELECT list_date FROM stock_basic WHERE ts_code='600000.SH'")).scalar())=='1999-11-10'
        with pytest.raises(ValueError):writer.ensure_from_snapshot(snapshot,['920001.BJ'],owner_guard=lambda session:None)
    finally:engine.dispose()

def test_reference_writer_rolls_back_new_rows_when_existing_ipo_conflicts():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from app.nightly.reference import SnapshotReferenceWriter
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
        conn.execute(text("INSERT INTO stock_basic VALUES ('600000.SH','600000','旧名称',NULL,NULL,'1999-11-10')"))
    snapshot=freeze_market(DAY,[reference(),reference('600000.SH',exchange='SSE')],'a'*64,DAY+'T20:00:00+08:00')
    writer=SnapshotReferenceWriter(sessionmaker(bind=engine))
    try:
        with pytest.raises(PreparationFailure,match='INVALID_EXISTING_DATA'):
            writer.ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lambda session:None)
        with engine.connect() as conn:
            assert conn.execute(text('SELECT COUNT(*) FROM stock_basic')).scalar()==1
    finally:engine.dispose()

def test_reference_owner_fence_prevents_all_reference_writes():
    from sqlalchemy import create_engine,text
    from sqlalchemy.orm import sessionmaker
    from app.nightly.reference import SnapshotReferenceWriter
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE stock_basic(ts_code TEXT PRIMARY KEY,symbol TEXT,name TEXT,area TEXT,industry TEXT,list_date TEXT)'))
    snapshot=freeze_market(DAY,[reference()],'a'*64,DAY+'T20:00:00+08:00')
    def lost(session):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
    try:
        with pytest.raises(RuntimeError,match='OWNER'):
            SnapshotReferenceWriter(sessionmaker(bind=engine)).ensure_from_snapshot(snapshot,snapshot['codes'],owner_guard=lost)
        with engine.connect() as conn:assert conn.execute(text('SELECT COUNT(*) FROM stock_basic')).scalar()==0
    finally:engine.dispose()
