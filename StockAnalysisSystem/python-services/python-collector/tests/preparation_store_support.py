"""Real isolated SQL fixtures. Never create tables in the application database."""
from contextlib import contextmanager
import importlib
import os
from pathlib import Path
import re
import sys

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


DDL = [
    'CREATE TABLE stock_pool_version (pool_version TEXT PRIMARY KEY, pool_id TEXT NOT NULL, metadata_json TEXT NOT NULL, member_sha256 TEXT NOT NULL, stock_count INTEGER NOT NULL, created_at TEXT NOT NULL)',
    'CREATE TABLE stock_pool_member (pool_version TEXT NOT NULL, ts_code TEXT NOT NULL, ordinal INTEGER NOT NULL, member_json TEXT NOT NULL, PRIMARY KEY(pool_version,ts_code), UNIQUE(pool_version,ordinal))',
    'CREATE TABLE stock_pool_active (pool_id TEXT PRIMARY KEY, pool_version TEXT NOT NULL)',
    'CREATE TABLE data_preparation_task (task_id TEXT PRIMARY KEY, identity TEXT NOT NULL, active_identity TEXT UNIQUE, request_json TEXT NOT NULL, status TEXT NOT NULL, phase TEXT NOT NULL, owner_token TEXT, owner_json TEXT, lease_until TEXT, retry_not_before TEXT, progress_json TEXT NOT NULL, result_json TEXT, error_kind TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)',
    'CREATE TABLE market_source_evidence (evidence_id TEXT PRIMARY KEY, kind TEXT NOT NULL, ts_code TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL, provider TEXT NOT NULL, relative_path TEXT NOT NULL, manifest_sha256 TEXT NOT NULL, content_sha256 TEXT NOT NULL, UNIQUE(kind,ts_code,start_date,end_date,content_sha256))',
]


def load(module, name):
    try:
        return getattr(importlib.import_module('app.repositories.' + module), name)
    except ModuleNotFoundError:
        pytest.fail('preparation SQL repository not implemented: ' + module)


@contextmanager
def database():
    if os.getenv('PREPARATION_TEST_MYSQL') == '1':
        # Explicit opt-in. Temporary tables shadow names only on this connection;
        # closing it removes every test row without touching permanent tables.
        analysis = Path(__file__).resolve().parents[2] / 'stock-analysis-app'
        sys.path.append(str(analysis))
        from database.db_connector import DatabaseConnector
        from dotenv import dotenv_values
        # The global pytest environment intentionally supplies fake credentials;
        # explicit temporary-table acceptance reads only the local opt-in config.
        project = analysis.parents[1]
        env_file = next((p for p in (analysis/'.env', project/'.env') if p.is_file()), None)
        values = dotenv_values(env_file) if env_file else {}
        if not values.get('DB_PASSWORD'):
            pytest.fail('explicit MySQL temporary test requires local DB configuration')
        config = dict(host=values.get('DB_HOST','localhost'), port=values.get('DB_PORT','3306'),
            user=values.get('DB_USER','root'), password=values['DB_PASSWORD'],
            database=values.get('DB_NAME','stock_analysis'), charset='utf8mb4', connect_timeout=10)
        connector = DatabaseConnector(config=config)
        conn = connector.engine.connect()
        try:
            migration = Path(__file__).resolve().parents[3] / 'infrastructure/mysql/migrations/V0.9_001_stock_pool_preparation.sql'
            script = migration.read_text(encoding='utf-8')
            script = re.sub(r'^--[^\n]*', '', script, flags=re.MULTILINE)
            # MySQL temporary tables cannot reference foreign keys. Permanent FK
            # installation is tested at the explicit migration acceptance step.
            script = re.sub(r'^\s*CONSTRAINT[^\n]+\n', '', script, flags=re.MULTILINE)
            script = re.sub(r',\s*\)', '\n)', script)
            script = script.replace('CREATE TABLE IF NOT EXISTS', 'CREATE TEMPORARY TABLE')
            for sql in script.split(';'):
                if sql.strip(): conn.execute(text(sql))
            conn.commit()
            yield sessionmaker(bind=conn)
        finally:
            conn.close()
            connector.close()
        return
    engine = create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        for stmt in DDL:
            conn.execute(text(stmt))
    factory = sessionmaker(bind=engine)
    try:
        yield factory
    finally:
        engine.dispose()


def pool(code='000001.SZ'):
    from app.preparation.contracts import freeze_pool
    return freeze_pool(dict(pool_id='industry:银行', display_name='银行', group_type='industry',
        group_code='银行', provider='akshare.eastmoney', requested_as_of='2026-10-11',
        provider_snapshot_date=None, acquired_at='2026-10-11T17:00:00+08:00'), [{'ts_code':code}])


def request(version):
    return dict(pool_version=version, requested_start='2026-09-28', requested_end='2026-09-30',
                requirements_version='market-v1')

NOW = '2026-10-11T09:00:00Z'
LATER = '2026-10-11T09:02:00Z'
OWNER = dict(token='1'*32, pid=101, started_at=NOW, host='local-test')
