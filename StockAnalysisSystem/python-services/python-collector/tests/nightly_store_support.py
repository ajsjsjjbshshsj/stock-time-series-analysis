"""Isolated nightly SQL state, with optional connection-local MySQL tables."""
from contextlib import contextmanager
from pathlib import Path
import os,re,sys
from sqlalchemy import text
sys.path.insert(0,str(Path(__file__).resolve().parent))
from preparation_store_support import database as base_database

NOW='2026-09-28T12:00:00Z'
UNTIL='2026-09-28T12:02:00Z'
OWNER=dict(token='1'*32,pid=101,started_at=NOW,host='local-test')
SQLITE_DDL='''CREATE TABLE market_update_run (
 task_id TEXT PRIMARY KEY,identity TEXT NOT NULL UNIQUE,trade_date TEXT NOT NULL,
 market_scope TEXT NOT NULL,requirements_version TEXT NOT NULL,request_json TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,universe_json TEXT,universe_sha256 TEXT,attempt_no INTEGER NOT NULL,
 not_before TEXT NOT NULL,status TEXT NOT NULL,phase TEXT NOT NULL,owner_token TEXT,owner_json TEXT,
 lease_until TEXT,retry_not_before TEXT,progress_json TEXT NOT NULL,result_json TEXT,error_kind TEXT,
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(market_scope,trade_date,requirements_version))'''

@contextmanager
def database():
    with base_database() as factory:
        with factory.begin() as session:
            if os.getenv('PREPARATION_TEST_MYSQL')=='1':
                path=Path(__file__).resolve().parents[3]/'infrastructure/mysql/migrations/V0.9_002_nightly_market_updates.sql'
                script=re.sub(r'^--[^\n]*','',path.read_text(encoding='utf-8'),flags=re.M)
                script=script.replace('CREATE TABLE IF NOT EXISTS','CREATE TEMPORARY TABLE')
                session.execute(text(script.strip().rstrip(';')))
            else:session.execute(text(SQLITE_DDL))
        yield factory

def store(factory):
    from app.repositories.market_update_repository import MarketUpdateRepository
    return MarketUpdateRepository(factory)

def submit(repo,day='2026-09-28',now=NOW):
    return repo.submit(day,scheduled_for=day+'T20:00:00+08:00',now=now)

def claim(repo):return repo.claim(OWNER,now=NOW,lease_until=UNTIL)
