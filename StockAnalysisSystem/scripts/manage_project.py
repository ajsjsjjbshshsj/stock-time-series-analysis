"""Explicit project setup/diagnosis; never starts collection or training."""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[1]
ANALYSIS = PROJECT / 'python-services/stock-analysis-app'
METADATA_TABLES = {
    'stock_pool_version', 'stock_pool_member', 'stock_pool_active',
    'data_preparation_task', 'market_source_evidence', 'market_update_run',
}


def load_environment(project):
    """Process environment takes precedence; only the shared example's peer is read."""
    from dotenv import dotenv_values
    return {**dotenv_values(project / '.env'), **os.environ}


def connector(env=None):
    # No SDK or application launcher imports. Existing DB seam owns credentials.
    sys.path.insert(0, str(ANALYSIS))
    from database.db_connector import DatabaseConnector
    config = None
    if env is not None:
        config = dict(host=env.get('DB_HOST', 'localhost'), port=env.get('DB_PORT', '3306'),
                      user=env.get('DB_USER', 'root'), password=env['DB_PASSWORD'],
                      database=env.get('DB_NAME', 'stock_analysis'), charset='utf8mb4',
                      connect_timeout=5, read_timeout=5, write_timeout=5, pool_timeout=5)
    return DatabaseConnector(config, diagnostic=True)


def schema_issues(connection):
    from sqlalchemy import inspect, Numeric
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    required = {'stock_basic', 'stock_daily', 'stock_daily_basic',
                'stock_constituent', 'analysis_result', 'stock_features', 'collection_task'}
    missing = (required | METADATA_TABLES) - tables
    issues = ['MIGRATION_REQUIRED: ' + ', '.join(sorted(missing))] if missing else []
    if 'stock_daily' in tables:
        columns = {column['name']: column['type'] for column in inspector.get_columns('stock_daily')}
        expected = {name: (20, 6) for name in ('open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg')}
        expected.update(vol=(24, 4), amount=(24, 4))
        if any(not isinstance(columns.get(name), Numeric)
               or (columns[name].precision, columns[name].scale) != spec
               for name, spec in expected.items()):
            issues.append('SCHEMA_CHECK_REQUIRED: stock_daily precision')
        indexes = inspector.get_indexes('stock_daily') + inspector.get_unique_constraints('stock_daily')
        if not any(index.get('unique', True) and index.get('column_names') == ['ts_code', 'trade_date']
                   for index in indexes):
            issues.append('SCHEMA_CHECK_REQUIRED: stock_daily unique key')
    return issues


def fresh_metadata():
    """Clone ORM schema, correcting the old Float contract only for NEW databases."""
    sys.path.insert(0, str(ANALYSIS))
    from sqlalchemy import MetaData, Numeric
    from database.models import Base
    result = MetaData()
    for table in Base.metadata.sorted_tables:
        table.to_metadata(result)
    daily = result.tables['stock_daily']
    for column in ('open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg'):
        daily.c[column].type = Numeric(20, 6)
    for column in ('vol', 'amount'):
        daily.c[column].type = Numeric(24, 4)
    return result


def migration_statements(project):
    for name in ('V0.9_001_stock_pool_preparation.sql', 'V0.9_002_nightly_market_updates.sql'):
        path = project / 'infrastructure/mysql/migrations' / name
        content = '\n'.join(line for line in path.read_text(encoding='utf-8').splitlines()
                            if not line.lstrip().startswith('--'))
        yield from (statement.strip() for statement in content.split(';') if statement.strip())


def initialize_empty(engine, metadata_factory=fresh_metadata, project=PROJECT):
    """Nonempty means ANY table or view. MySQL DDL is not transactionally reversible."""
    from sqlalchemy import inspect, text
    with engine.connect() as connection:
        inspector = inspect(connection)
        if inspector.get_table_names() or inspector.get_view_names():
            raise RuntimeError('NONEMPTY_SCHEMA: restore/upgrade explicitly; no DDL performed')
        metadata = metadata_factory()
        statements = list(migration_statements(project)) if engine.dialect.name == 'mysql' else []
        metadata.create_all(connection)
        for statement in statements:
            connection.execute(text(statement))
        connection.commit()


def doctor(project, env, profile):
    """Read-only prerequisites and schema inspection; readiness is not model quality."""
    errors = []
    required = ['DB_PASSWORD', 'TUSHARE_TOKEN']
    packages = ['dotenv', 'sqlalchemy', 'pymysql', 'pandas', 'pyarrow', 'tushare']
    if profile == 'full':
        required += ['CLICKHOUSE_PASSWORD']
        packages += ['fastapi', 'uvicorn']
        for executable in ('java', 'mvn', 'node', 'npm', 'docker'):
            if not shutil.which(executable):
                errors.append('MISSING_EXECUTABLE: ' + executable)
    for key in required:
        if not env.get(key):
            errors.append('MISSING_CONFIG: ' + key)
    for package in packages:
        if importlib.util.find_spec(package) is None:
            errors.append('MISSING_PACKAGE: ' + package)
    if not errors:
        db = None
        try:
            # The CLI applies this environment before calling doctor; injected env
            # in library callers is for prerequisite checks, not credential logging.
            db = connector(env)
            with db.engine.connect() as connection:
                errors.extend(schema_issues(connection))
        except Exception:
            errors.append('DATABASE_UNAVAILABLE: check credentials/schema/permissions')
        finally:
            if db is not None:
                db.close()
        if profile == 'full':
            from urllib.parse import urlsplit
            clickhouse = urlsplit(env.get('CLICKHOUSE_HTTP_URL', 'http://127.0.0.1:8123'))
            kafka = urlsplit('tcp://' + env.get('KAFKA_BOOTSTRAP_SERVERS', 'localhost:9092').split(',')[0])
            for host, port, name in ((clickhouse.hostname, clickhouse.port or 8123, 'ClickHouse'),
                                      (kafka.hostname, kafka.port or 9092, 'Kafka')):
                try:
                    with socket.create_connection((host, port), timeout=2):
                        pass
                except OSError:
                    errors.append('ENDPOINT_UNAVAILABLE: ' + name)
    for error in errors:
        print(error)
    print('DATA_AND_MODELS: separate artifacts; no acquisition/training performed')
    print('PREREQUISITES_OK (not prediction readiness)' if not errors else 'NOT_READY')
    return int(bool(errors))


def service_command(project, service):
    if service == 'prediction':
        return ([sys.executable, '-m', 'uvicorn', 'api.forecast:create_app', '--factory',
                 '--host', '127.0.0.1', '--port', '8084'],
                project / 'python-services/stock-analysis-app', {})
    if service == 'frontend':
        return (['npm', 'run', 'dev', '--', '--host', '127.0.0.1'], project / 'frontend', {})
    if service in ('consumer', 'market'):
        module = 'kafka-consumer-service' if service == 'consumer' else 'market-api-service'
        return (['mvn', 'spring-boot:run'], project / 'java-services' / module,
                {'MARKET_LISTENER_ENABLED': 'false'} if service == 'market' else {})
    raise ValueError('UNKNOWN_SERVICE')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    check = commands.add_parser('doctor', help='read-only prerequisite/schema checks')
    check.add_argument('--profile', choices=('collector', 'full'), default='collector')
    init = commands.add_parser('init-db', help='initialize an EXISTING, EMPTY MySQL schema')
    init.add_argument('--empty-only', action='store_true', required=True)
    start = commands.add_parser('start', help='foreground service only; Ctrl+C to stop')
    start.add_argument('--service', choices=('frontend', 'prediction', 'consumer', 'market'), required=True)
    args = parser.parse_args(argv)
    try:
        env = load_environment(PROJECT)
        os.environ.update({key: value for key, value in env.items() if value is not None})
        if args.command == 'doctor':
            return doctor(PROJECT, env, args.profile)
        if args.command == 'init-db':
            db = connector()
            try:
                initialize_empty(db.engine)
            finally:
                db.close()
            print('EMPTY_SCHEMA_INITIALIZED: no data/models collected')
            return 0
        command, cwd, updates = service_command(PROJECT, args.service)
        executable = shutil.which(command[0])
        if not executable:
            print('MISSING_EXECUTABLE: ' + Path(command[0]).name)
            return 1
        command[0] = executable
        return subprocess.call(command, cwd=cwd, env={**os.environ, **updates})
    except Exception:
        # Raw driver/subprocess exceptions may contain credentials; never echo them.
        print('OPERATION_FAILED: check environment, dependencies and schema; no automatic repair')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
