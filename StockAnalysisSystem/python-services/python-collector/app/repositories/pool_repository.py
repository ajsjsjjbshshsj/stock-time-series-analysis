"""Immutable fixed versions, stable member ordinals and an explicit active pointer."""
from datetime import datetime, timezone
import json

from sqlalchemy import text

from app.preparation.contracts import validate_pool
from ._preparation_sql import transaction, encode, current_lock, stamp


class PoolRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _load(session, version):
        row = session.execute(text('SELECT * FROM stock_pool_version WHERE pool_version=:v'),
                              dict(v=version)).mappings().first()
        if row is None:
            raise ValueError('POOL_NOT_FOUND')
        pool = validate_pool(json.loads(row['metadata_json']))
        members = session.execute(text('SELECT * FROM stock_pool_member WHERE pool_version=:v ORDER BY ordinal'),
                                  dict(v=version)).mappings().all()
        if (row['pool_version'] != pool['pool_version'] or row['pool_id'] != pool['pool_id']
                or row['member_sha256'] != pool['member_sha256'] or row['stock_count'] != pool['stock_count']
                or len(members) != len(pool['members'])
                or any(r['ordinal'] != i or r['ts_code'] != pool['members'][i]['ts_code']
                       or json.loads(r['member_json']) != pool['members'][i] for i, r in enumerate(members))):
            raise ValueError('PERSISTED_POOL_MISMATCH')
        return pool

    def register(self, pool: dict, *, activate: bool = False) -> dict:
        validate_pool(pool)
        with transaction(self.session_factory) as session:
            row = session.execute(text('SELECT pool_version FROM stock_pool_version WHERE pool_version=:v'
                                       + current_lock(session)), dict(v=pool['pool_version'])).first()
            if row:
                stored = self._load(session, pool['pool_version'])
                audit = {'requested_as_of', 'acquired_at', 'display_name'}
                if {k:v for k,v in pool.items() if k not in audit} != {k:v for k,v in stored.items() if k not in audit}:
                    raise ValueError('IMMUTABLE_POOL_CHANGED')
                pool = stored
            else:
                session.execute(text('INSERT INTO stock_pool_version '
                    '(pool_version,pool_id,metadata_json,member_sha256,stock_count,created_at) '
                    'VALUES (:v,:p,:data,:sha,:count,:now)'), dict(v=pool['pool_version'], p=pool['pool_id'],
                    data=encode(pool), sha=pool['member_sha256'], count=pool['stock_count'],
                    now=stamp(datetime.now(timezone.utc))))
                for i, member in enumerate(pool['members']):
                    session.execute(text('INSERT INTO stock_pool_member '
                        '(pool_version,ts_code,ordinal,member_json) VALUES (:v,:code,:i,:data)'),
                        dict(v=pool['pool_version'], code=member['ts_code'], i=i, data=encode(member)))
            if activate:
                existing = session.execute(text('SELECT pool_version FROM stock_pool_active WHERE pool_id=:p'
                    + current_lock(session)), dict(p=pool['pool_id'])).first()
                if existing:
                    session.execute(text('UPDATE stock_pool_active SET pool_version=:v WHERE pool_id=:p'),
                                    dict(v=pool['pool_version'], p=pool['pool_id']))
                else:
                    session.execute(text('INSERT INTO stock_pool_active (pool_id,pool_version) VALUES (:p,:v)'),
                                    dict(v=pool['pool_version'], p=pool['pool_id']))
            return pool

    def resolve(self, *, pool_id: str | None = None, pool_version: str | None = None) -> dict:
        if (pool_id is None) == (pool_version is None):
            raise ValueError('EXACTLY_ONE_POOL_SELECTOR_REQUIRED')
        with transaction(self.session_factory) as session:
            if pool_id is not None:
                row = session.execute(text('SELECT pool_version FROM stock_pool_active WHERE pool_id=:p'),
                                      dict(p=pool_id)).first()
                if row is None:
                    raise ValueError('ACTIVE_POOL_NOT_FOUND')
                pool_version = row[0]
            return self._load(session, pool_version)

    def list_versions(self, pool_id: str | None = None) -> list[dict]:
        with transaction(self.session_factory) as session:
            where = ' WHERE pool_id=:p' if pool_id is not None else ''
            rows = session.execute(text('SELECT pool_version FROM stock_pool_version'+where+
                                        ' ORDER BY pool_id,pool_version'), dict(p=pool_id)).all()
            return [self._load(session, r[0]) for r in rows]
