"""Immutable staged-generation bindings, separate from activation/readiness.

No absolute machine paths are persisted. The configured snapshot root is local;
the stored reference is exactly its generation-directory name. Registering a
generation atomically binds a DISABLED route, never acknowledges seed coverage.
"""
from hashlib import sha256
import time

from sqlalchemy import MetaData, Table, Column, String, Integer, BigInteger, inspect, select

from .capture import _metadata_compatible, require_capture
from .outbox import Outbox, identity, integer, routes
from .snapshot import _json, _safe_folder, verify_snapshot


metadata = MetaData()
generations = Table('indicator_generation', metadata,
    Column('id', String(32), primary_key=True),
    Column('state', String(16), nullable=False),
    Column('snapshot_ref', String(32), nullable=False, unique=True),
    Column('manifest_sha256', String(64), nullable=False),
    Column('input_topic', String(249), nullable=False, unique=True),
    Column('output_topic', String(249), nullable=False, unique=True),
    Column('partitions', Integer, nullable=False),
    Column('cutoff', String(10), nullable=False),
    Column('source_rows', BigInteger, nullable=False),
    Column('source_stocks', Integer, nullable=False),
    Column('covered_ids', BigInteger, nullable=False),
    Column('created_at', BigInteger, nullable=False), mysql_engine='InnoDB')


def migrate_generations(engine):
    """Explicit additive migration only; requires a compatible capture install."""
    with engine.connect() as connection:
        require_capture(connection)
    metadata.create_all(engine)
    _metadata_compatible(inspect(engine), engine.dialect, tables=metadata.tables.values())
    if engine.dialect.name == 'mysql':
        from sqlalchemy import text
        with engine.connect() as connection:
            storage = connection.execute(text("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='indicator_generation'")).scalar_one()
            if storage.upper() != 'INNODB':
                raise ValueError('Transactional indicator generation registry required')


class Generations:
    def __init__(self, factory, snapshot_root, *, clock=None):
        self.factory = factory
        self.snapshot_root = _safe_folder(snapshot_root)
        self.clock = clock or time.time
        self.outbox = Outbox(factory, clock=self.clock)

    def stage(self, generation, *, partitions):
        identity(generation)
        integer(partitions, 1, 256)
        manifest = verify_snapshot(self.snapshot_root / generation)
        digest = sha256(_json(manifest)).hexdigest()
        binding = dict(id=generation, state='STAGED', snapshot_ref=generation,
            manifest_sha256=digest, input_topic=f'stock.indicators.{generation}.raw.v2',
            output_topic=f'stock.indicators.{generation}.daily.v2', partitions=partitions,
            cutoff=manifest['end'], source_rows=manifest['rows'],
            source_stocks=manifest['stocks'], covered_ids=manifest['covered']['rows'])
        with self.outbox.transaction() as session:
            prior = session.execute(select(generations).where(generations.c.id == generation).with_for_update()).mappings().one_or_none()
            route = session.execute(select(routes).where(routes.c.id == generation).with_for_update()).mappings().one_or_none()
            if route is not None and (route['enabled'] or route['input_topic'] != binding['input_topic']):
                raise ValueError('Generation route must be disabled and bound exactly')
            if prior is not None:
                if any(prior[key] != value for key, value in binding.items()) or route is None:
                    raise ValueError('Immutable indicator generation binding changed')
                return dict(prior)
            now = int(self.clock())
            if route is None:
                session.execute(routes.insert().values(id=generation, input_topic=binding['input_topic'], enabled=False, created_at=now))
            session.execute(generations.insert().values(**binding, created_at=now))
            return dict(binding, created_at=now)

    def get(self, generation, *, verify=False):
        identity(generation)
        with self.factory() as session:
            row = session.execute(select(generations).where(generations.c.id == generation)).mappings().one_or_none()
            if row is None:
                raise ValueError('Indicator generation not registered')
            result = dict(row)
        if result['snapshot_ref'] != generation:
            raise ValueError('Indicator generation reference changed')
        if verify:
            manifest = verify_snapshot(self.snapshot_root / generation, expected_sha256=result['manifest_sha256'])
            if (manifest['rows'] != result['source_rows'] or manifest['stocks'] != result['source_stocks']
                    or manifest['covered']['rows'] != result['covered_ids'] or manifest['end'] != result['cutoff']):
                raise ValueError('Indicator generation source binding mismatch')
        return result
