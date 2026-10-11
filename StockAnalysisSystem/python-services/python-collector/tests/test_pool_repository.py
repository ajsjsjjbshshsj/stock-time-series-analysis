from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest
from sqlalchemy import text
from preparation_store_support import database, load, pool


def test_same_pool_reuses_first_audit_and_transactional_members():
    cls = load('pool_repository', 'PoolRepository')
    with database() as factory:
        repo = cls(factory)
        original = pool()
        saved = repo.register(original, activate=True)
        retry = dict(original, acquired_at='2026-10-12T17:00:00+08:00', requested_as_of='2026-10-12')
        assert repo.register(retry) == saved
        assert repo.resolve(pool_id='industry:银行')['codes'] == ['000001.SZ']
        assert len(repo.list_versions()) == 1


def test_active_switch_does_not_mutate_frozen_old_members():
    cls = load('pool_repository', 'PoolRepository')
    with database() as factory:
        repo = cls(factory)
        first = repo.register(pool(), activate=True)
        next_pool = repo.register(pool('600000.SH'), activate=True)
        assert repo.resolve(pool_id='industry:银行')['pool_version'] == next_pool['pool_version']
        assert repo.resolve(pool_version=first['pool_version'])['codes'] == ['000001.SZ']


@pytest.mark.parametrize('mutation', ['members', 'mapping', 'hash'])
def test_tampered_persisted_pool_rejected(mutation):
    cls = load('pool_repository', 'PoolRepository')
    with database() as factory:
        repo = cls(factory)
        saved = repo.register(pool(), activate=True)
        with factory.begin() as session:
            if mutation == 'mapping':
                session.execute(text('UPDATE stock_pool_member SET ordinal=5'))
            elif mutation == 'members':
                session.execute(text("UPDATE stock_pool_member SET ts_code='600000.SH'"))
            else:
                session.execute(text("UPDATE stock_pool_version SET member_sha256='bad'"))
        with pytest.raises(ValueError):
            repo.resolve(pool_version=saved['pool_version'])


def test_missing_migration_never_creates_tables():
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import sessionmaker
    cls = load('pool_repository', 'PoolRepository')
    engine = create_engine('sqlite:///:memory:')
    try:
        with pytest.raises(RuntimeError, match='MIGRATION_REQUIRED'):
            cls(sessionmaker(bind=engine)).list_versions()
        assert inspect(engine).get_table_names() == []
    finally:
        engine.dispose()
