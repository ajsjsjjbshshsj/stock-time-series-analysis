"""Explicit local projection operations; importing/help never opens dependencies.

No command starts a job, creates topics, resets offsets, downloads quotes or
trains a model. Migration is an operator-attested maintenance operation, not
proof that a backup can be restored or another machine's writers have stopped.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys


def _identity(value):
    if not re.fullmatch('[0-9a-f]{32}', value):
        raise ValueError('32 lowercase hexadecimal characters required')
    return value


def _expected(value):
    return None if value == 'none' else _identity(value)


def _bound(low, high):
    def parse(value):
        number = int(value)
        if not low <= number <= high:
            raise ValueError('Bound exceeded')
        return number
    return parse


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--snapshot-root', help='Relative to project root unless absolute')
    result.add_argument('--calendar-root', help='Existing sealed preparation calendar root; no provider calls')
    commands = result.add_subparsers(dest='command', required=True)
    commands.add_parser('status', help='Bounded metadata only; never asserts indicator readiness')
    migration = commands.add_parser('migrate', help='Explicit additive schema only; no route is enabled')
    migration.add_argument('--backup-file', required=True)
    migration.add_argument('--backup-sha256', required=True)
    migration.add_argument('--confirm-backup-and-idle', action='store_true', required=True,
        help='Attest restorable backup and stopped writers on every machine')
    for name in ('export', 'stage', 'seed', 'verify-seed', 'ack-covered', 'enable-tail', 'tail-once', 'activate', 'refresh', 'rollback'):
        command = commands.add_parser(name)
        if name not in ('refresh', 'rollback'):
            command.add_argument('--generation', required=True, type=_identity)
        if name == 'export': command.add_argument('--batch-size', type=_bound(1, 5000), default=500)
        if name == 'stage': command.add_argument('--partitions', type=_bound(1, 256), required=True)
        if name == 'ack-covered': command.add_argument('--batch-size', type=_bound(1, 5000), default=1000)
        if name in ('verify-seed', 'enable-tail', 'activate', 'refresh', 'rollback'):
            command.add_argument('--job-id', required=True, type=_identity,
                help='Known deployed V2 artifact/job identity; REST cannot authenticate arbitrary binaries')
        if name in ('activate', 'refresh', 'rollback'):
            command.add_argument('--expected-epoch', required=True, type=_bound(0, 2**63-2))
            command.add_argument('--expected-generation', required=True, type=_expected,
                help='Exact current generation, or literal none for initial activation')
    return result


def _backup(args):
    from .snapshot import _safe_folder
    if not re.fullmatch('[0-9a-f]{64}', args.backup_sha256):
        raise ValueError('Explicit backup digest required')
    path = _safe_folder(args.backup_file)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('Nonempty backup file required')
    digest = sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != args.backup_sha256:
        raise ValueError('Backup file identity changed')


def _database(project):
    # Reuse the sole database connector, never the legacy auto-create initializer.
    application = str(project/'python-services/stock-analysis-app')
    if application not in sys.path: sys.path.insert(0, application)
    from database.db_connector import DatabaseConnector
    from config.settings import DATABASE_CONFIG
    return DatabaseConnector(dict(DATABASE_CONFIG, connect_timeout=5,
        read_timeout=300, write_timeout=30, pool_timeout=5), diagnostic=True)


def _root(project, configured, default):
    from .snapshot import _safe_folder
    path = Path(configured or default)
    return _safe_folder(path if path.is_absolute() else project/path)


def _required(env, key):
    value = env.get(key)
    if not value: raise ValueError('Explicit dependency configuration required')
    return value


def _status(engine):
    from sqlalchemy import inspect, select
    from .outbox import metadata as capture
    from .generation_repository import metadata as registry, generations
    from .seed import metadata as seed
    from .lifecycle import metadata as authority, active
    tables = set(inspect(engine).get_table_names())
    expected = set(capture.tables) | set(registry.tables) | set(seed.tables) | set(authority.tables)
    result = dict(installed=expected.issubset(tables), authority_status_only=True, active=None, generations=[])
    with engine.connect() as connection:
        if active.name in tables:
            row = connection.execute(select(active).where(active.c.slot == 'daily')).mappings().one_or_none()
            result['active'] = dict(row) if row is not None else None
        if generations.name in tables:
            rows = connection.execute(select(generations.c.id, generations.c.state,
                generations.c.cutoff, generations.c.source_rows, generations.c.source_stocks)
                .order_by(generations.c.created_at.desc(), generations.c.id).limit(20)).mappings()
            result['generations'] = [dict(row) for row in rows]
    return result


def _execute(args, db, project, env, now):
    if args.command == 'status': return _status(db.engine)
    if args.command == 'migrate':
        from .capture import migrate_capture
        from .generation_repository import migrate_generations
        from .seed import migrate_seed
        from .lifecycle import migrate_projection
        for migration in (migrate_capture, migrate_generations, migrate_seed, migrate_projection):
            migration(db.engine)
        return dict(metadata_installed=True, routes_unchanged=True, backup_sha256=args.backup_sha256)
    from .generation_repository import Generations
    snapshot_root = _root(project, args.snapshot_root or env.get('INDICATOR_SNAPSHOT_ROOT'), '.runtime/indicator_snapshots')
    registry = Generations(db.Session, snapshot_root)
    if args.command == 'stage':
        return registry.stage(args.generation, partitions=args.partitions)
    from app.preparation.calendar import CalendarStore
    calendar_root = _root(project, args.calendar_root or env.get('PREPARATION_DATA_ROOT'), '.runtime/stock_pool_preparation')
    if args.command == 'export':
        from .source_guard import completed_cutoff
        from .snapshot import export_snapshot, verify_snapshot
        calendar = CalendarStore(calendar_root, None, None).cached()
        cutoff = completed_cutoff(calendar, now)
        folder = export_snapshot(db.engine, snapshot_root, args.generation, end=cutoff, batch_size=args.batch_size)
        manifest = verify_snapshot(folder)
        return dict(generation=args.generation, cutoff=cutoff, rows=manifest['rows'], stocks=manifest['stocks'], state='STAGED')
    if args.command == 'seed':
        from .seed import KafkaSeedTransport, publish_seed
        transport = KafkaSeedTransport({'bootstrap.servers': _required(env, 'KAFKA_BOOTSTRAP_SERVERS')}, args.generation)
        publish_seed(registry, args.generation, transport)
        return dict(generation=args.generation, state='SEEDED', indicator_ready=False)
    if args.command == 'tail-once':
        from .tail import KafkaTailTransport, TailPublisher
        transport = KafkaTailTransport({'bootstrap.servers': _required(env, 'KAFKA_BOOTSTRAP_SERVERS')}, args.generation)
        delivered = TailPublisher(registry, args.generation, transport).run_once()
        return dict(generation=args.generation, delivered=delivered)
    from .lifecycle import Lifecycle
    if args.command == 'ack-covered':
        # This operation needs the saved SEED proof, not live external reads.
        return Lifecycle(registry, None, None).ack_covered(args.generation, batch_size=args.batch_size)
    from .readers import ClickHouseProjectionReader, FlinkCheckpointReader
    from .source_guard import MySqlSourceGuard
    checkpoint = FlinkCheckpointReader(_required(env, 'FLINK_REST_URL'), args.job_id)
    with ClickHouseProjectionReader(_required(env, 'CLICKHOUSE_HTTP_URL'), _required(env, 'CLICKHOUSE_DB'),
            _required(env, 'CLICKHOUSE_USER'), _required(env, 'CLICKHOUSE_PASSWORD'), checkpoint) as reader:
        calendar = CalendarStore(calendar_root, None, None).cached()
        lifecycle = Lifecycle(registry, reader, MySqlSourceGuard(db.engine, calendar))
        if args.command == 'verify-seed': return lifecycle.verify_seed(args.generation)
        if args.command == 'enable-tail':
            lifecycle.enable_tail(args.generation)
            return dict(generation=args.generation, tail_enabled=True, indicator_ready=False)
        expected = dict(expected_epoch=args.expected_epoch, expected_generation=args.expected_generation)
        if args.command == 'activate': return lifecycle.activate(args.generation, **expected)
        if args.command == 'refresh': return lifecycle.refresh(**expected)
        if args.command == 'rollback': return lifecycle.rollback(**expected)
    raise ValueError('Unknown projection operation')


def main(argv=None, *, database_factory=None, project_root=None, environ=None, now=None):
    args = parser().parse_args(argv)
    project = Path(project_root).resolve() if project_root is not None else Path(__file__).resolve().parents[4]
    try:
        if args.command == 'migrate': _backup(args)
        if environ is None:
            from dotenv import load_dotenv
            for path in (project/'python-services/python-collector/.env', project/'.env'):
                if path.is_file():
                    load_dotenv(path)
                    break
            environ = os.environ
        with ExitStack() as owned:
            db = database_factory() if database_factory else _database(project)
            if database_factory is None: owned.callback(db.close)
            result = _execute(args, db, project, environ, now or datetime.now(timezone.utc))
            print(json.dumps(result, ensure_ascii=True, allow_nan=False))
        return 0
    except Exception:
        # Dependency exceptions may include credentials, internal paths and SQL.
        print('Indicator operation failed; check explicit migration, sealed inputs, bindings and dependencies. No readiness inferred.', file=sys.stderr)
        return 1

