"""Immutable repeatable-read bootstrap inputs; exporting never activates a route.

The explicit covered-ID file is essential: an allocated but uncommitted lower
outbox ID must NOT be skipped when a larger ID was visible to this snapshot.
Only later seed/checkpoint/ingestion proof may acknowledge covered deliveries.
"""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re

from sqlalchemy import text

from app.events.event_factory import EventFactory
from app.kafka.serializer import KafkaJsonSerializer
from app.models.stock_daily import StockDailyRecord
from .capture import FIELDS, require_capture, source_scope
from .outbox import identity, integer


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')


def _date(value):
    if type(value) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) is None:
        raise ValueError('ISO source cutoff required')
    result = date.fromisoformat(value)
    if result < date(2023, 1, 1):
        raise ValueError('Source cutoff precedes capture floor')
    return result


def _linked(path):
    # Python3.11 is a supported container runtime. Windows junctions are reparse
    # points even though Path.is_junction() only exists from Python3.12 onward.
    try:
        return path.is_symlink() or bool(getattr(path.lstat(), 'st_file_attributes', 0) & 0x400)
    except FileNotFoundError:
        return False


def _safe_folder(folder):
    folder = Path(folder).absolute()
    if any(_linked(p) for p in (folder, *folder.parents)):
        raise ValueError('Linked snapshot directories are not allowed')
    return folder


@contextmanager
def _snapshot(engine):
    with engine.connect() as connection:
        if engine.dialect.name == 'mysql':
            connection = connection.execution_options(isolation_level='REPEATABLE READ')
        elif engine.dialect.name != 'sqlite':
            raise ValueError('Unsupported snapshot database')
        with connection.begin():
            if engine.dialect.name == 'sqlite':
                connection.exec_driver_sql('BEGIN')
            yield connection


class _Shard:
    def __init__(self, folder, path):
        self.path = path
        self.file = (folder / path).open('xb')
        self.digest = sha256()
        self.rows = 0
        self.size = 0

    def append(self, line):
        self.file.write(line)
        self.digest.update(line)
        self.size += len(line)
        self.rows += 1

    def close(self):
        if not self.file.closed:
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()

    def description(self):
        return dict(path=self.path, sha256=self.digest.hexdigest(), bytes=self.size, rows=self.rows)


def export_snapshot(engine, root, generation, *, end, batch_size=500, progress=None):
    """Pin a genuine raw/capture view; caller pins the completed trading cutoff.

    Uses bounded database fetches and one open per-stock shard at a time. The
    destination is exclusive and never resumed/overwritten. Failed attempts stay
    unsealed for diagnosis; they cannot pass verification or become active.
    """
    identity(generation)
    cutoff = _date(end)
    integer(batch_size, 1, 5000)
    root = _safe_folder(root)
    root.mkdir(parents=True, exist_ok=True)
    folder = root / generation
    folder.mkdir()  # Existing generations are immutable, including failed ones.
    (folder / 'stocks').mkdir()
    opened = []
    try:
        with _snapshot(engine) as connection:
            source_schema = require_capture(connection)
            # First consistent data read fixes the view for BOTH subsequent files.
            covered = _Shard(folder, 'covered.jsonl')
            opened.append(covered)
            statement = text(f'SELECT o.id FROM indicator_outbox o WHERE {source_scope(engine.dialect.name, "o")} AND o.trade_date<=:end ORDER BY o.id')
            result = connection.execution_options(stream_results=True, yield_per=batch_size).execute(statement, dict(end=end))
            try:
                for row in result:
                    covered.append(str(row.id).encode('ascii') + b'\n')
            finally:
                result.close()
            covered.close()
            if progress:
                progress('covered', covered.rows)

            columns = ','.join(f'd.`{key}`' for key in FIELDS)
            statement = text(f'SELECT {columns}, COALESCE((SELECT MAX(o.id) FROM indicator_outbox o WHERE o.ts_code=d.ts_code AND o.trade_date=d.trade_date),0) AS source_revision FROM stock_daily d WHERE {source_scope(engine.dialect.name, "d")} AND d.trade_date<=:end ORDER BY d.ts_code,d.trade_date')
            result = connection.execution_options(stream_results=True, yield_per=batch_size).execute(statement, dict(end=end))
            shards = []
            current = None
            code = None
            total = 0
            ingested = datetime.now(timezone.utc)
            first_day = last_day = None
            try:
                for row in result.mappings():
                    if row['ts_code'] != code:
                        if current is not None:
                            current.close()
                            shards.append(dict(current.description(), code=code, first=first_day, last=last_day))
                        code = row['ts_code']
                        if re.fullmatch(r'(?:[0-9]{6})\.(?:SH|SZ)', code) is None:
                            raise ValueError('Invalid snapshot source code')
                        current = _Shard(folder, f'stocks/{code.replace(".", "_")}.jsonl')
                        opened.append(current)
                        first_day = last_day = None
                    day = row['trade_date']
                    day = _date(day.isoformat() if type(day) is date else day)
                    if day > cutoff or (last_day is not None and day.isoformat() <= last_day):
                        raise ValueError('Snapshot source order/date mismatch')
                    first_day = first_day or day.isoformat()
                    last_day = day.isoformat()
                    values = {key: row[key] for key in FIELDS}
                    values['trade_date'] = day
                    event = EventFactory.create_daily_event(StockDailyRecord(**values, source='MYSQL_SNAPSHOT'),
                        trace_id=f'indicator-snapshot:{generation}', ingest_time=ingested)
                    payload = KafkaJsonSerializer.serialize(event)
                    revision = integer(row['source_revision'], 0, 2**63 - 1)
                    prefix = _json(dict(schemaVersion=2, generation=generation, sourceRevision=revision))[:-1]
                    current.append(prefix + b',"event":' + payload + b'}\n')
                    total += 1
                    if progress and total % batch_size == 0:
                        progress('source', total)
            finally:
                result.close()
            if current is not None:
                current.close()
                shards.append(dict(current.description(), code=code, first=first_day, last=last_day))
            manifest = dict(schemaVersion=2, generation=generation, state='STAGED',
                start='2023-01-01', end=end, rows=total, stocks=len(shards),
                sourceSchema=source_schema, covered=covered.description(), shards=shards)
        manifest_bytes = _json(manifest)
        with (folder / 'manifest.json').open('xb') as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        seal = _json(dict(manifestSha256=sha256(manifest_bytes).hexdigest()))
        with (folder / '.seal.tmp').open('xb') as handle:
            handle.write(seal)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(folder / '.seal.tmp', folder / 'SEALED.json')
        return folder
    finally:
        for shard in opened:
            shard.close()


def _metadata_bytes(path, limit):
    if _linked(path) or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Invalid snapshot metadata file')
    with path.open('rb') as handle:
        body = handle.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Snapshot metadata exceeds bound')
    return body


def verify_snapshot(folder, *, expected_sha256=None):
    """Stream-check immutable files. Ledger callers must pin expected_sha256.

    A self-contained digest is integrity evidence, not external authorization or
    readiness. This verifier never acknowledges any outbox ID or activates data.
    """
    folder = _safe_folder(folder)
    try:
        identity(folder.name)
        seal = json.loads(_metadata_bytes(folder / 'SEALED.json', 1024))
        manifest_bytes = _metadata_bytes(folder / 'manifest.json', 16 * 1024 * 1024)
        manifest = json.loads(manifest_bytes)
        if manifest_bytes != _json(manifest):
            raise ValueError('Canonical snapshot manifest required')
        digest = sha256(manifest_bytes).hexdigest()
        if seal != dict(manifestSha256=digest) or (expected_sha256 is not None and digest != expected_sha256):
            raise ValueError('Snapshot manifest digest mismatch')
        if (manifest['schemaVersion'] != 2 or manifest['state'] != 'STAGED'
                or manifest['generation'] != folder.name or manifest['start'] != '2023-01-01'):
            raise ValueError('Snapshot manifest binding mismatch')
        _date(manifest['end'])
        integer(manifest['stocks'], 0, 10000)
        if len(manifest['shards']) != manifest['stocks']:
            raise ValueError('Snapshot stock count mismatch')
        expected_files = {'manifest.json', 'SEALED.json', 'covered.jsonl'}
        total = 0
        previous_code = ''
        for descriptor in [manifest['covered'], *manifest['shards']]:
            is_coverage = descriptor is manifest['covered']
            if is_coverage:
                path = 'covered.jsonl'
            else:
                code = descriptor['code']
                if re.fullmatch(r'(?:[0-9]{6})\.(?:SH|SZ)', code) is None or code <= previous_code:
                    raise ValueError('Snapshot code ordering mismatch')
                previous_code = code
                path = f'stocks/{code.replace(".", "_")}.jsonl'
                expected_files.add(path)
            if descriptor['path'] != path:
                raise ValueError('Snapshot shard path mismatch')
            file = folder / path
            _safe_folder(file.parent)
            if _linked(file) or not file.is_file():
                raise ValueError('Snapshot shard unavailable')
            digest = sha256()
            size = count = prior_id = 0
            prior_day = first_day = None
            with file.open('rb') as handle:
                while line := handle.readline(1024 * 1024 + 1):
                    if len(line) > 1024 * 1024 or not line.endswith(b'\n'):
                        raise ValueError('Invalid snapshot record framing')
                    digest.update(line)
                    size += len(line)
                    count += 1
                    if is_coverage:
                        source_id = int(line)
                        if str(source_id).encode() + b'\n' != line or source_id <= prior_id:
                            raise ValueError('Snapshot covered-ID ordering mismatch')
                        integer(source_id, 1, 2**63 - 1)
                        prior_id = source_id
                    else:
                        point = json.loads(line)
                        event = point['event']
                        day = _date(event['tradeDate']).isoformat()
                        if (point['schemaVersion'] != 2 or point['generation'] != folder.name
                                or event['tsCode'] != code or day > manifest['end']
                                or (prior_day is not None and day <= prior_day)):
                            raise ValueError('Snapshot record binding/order mismatch')
                        integer(point['sourceRevision'], 0, 2**63 - 1)
                        first_day = first_day or day
                        prior_day = day
            if (size != descriptor['bytes'] or count != descriptor['rows']
                    or digest.hexdigest() != descriptor['sha256']):
                raise ValueError('Snapshot shard digest/count mismatch')
            if not is_coverage:
                if count == 0 or first_day != descriptor['first'] or prior_day != descriptor['last']:
                    raise ValueError('Snapshot shard date bounds mismatch')
                total += count
        if total != manifest['rows']:
            raise ValueError('Snapshot total count mismatch')
        files = set()
        for child in folder.rglob('*'):
            if _linked(child):
                raise ValueError('Linked snapshot contents')
            if child.is_file():
                files.add(child.relative_to(folder).as_posix())
        if files != expected_files:
            raise ValueError('Snapshot unexpected/missing files')
        return manifest
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError('Snapshot verification failed') from error
