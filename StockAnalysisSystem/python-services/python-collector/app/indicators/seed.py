"""One-shot transactional bootstrap, distinct from verified/active readiness.

An ambiguous seed attempt is fail-closed: build a fresh isolated generation,
never replay an already committed historical prefix into bounded Flink state.
"""
from hashlib import sha256
import time

from sqlalchemy import MetaData, Table, Column, String, BigInteger, Integer, inspect, select, text

from .capture import _metadata_compatible
from .generation_repository import generations
from .outbox import identity, integer, routes
from .snapshot import verify_snapshot, _safe_folder, _linked
from .wire import snapshot_envelope, seed_end, partition_for, require_binding

metadata = MetaData()
receipts = Table('indicator_seed_receipt', metadata,
    Column('generation', String(32), primary_key=True),
    Column('ts_code', String(9), primary_key=True),
    Column('partition', Integer, nullable=False),
    Column('end_offset', BigInteger, nullable=False),
    Column('source_rows', BigInteger, nullable=False),
    Column('source_sha256', String(64), nullable=False), mysql_engine='InnoDB')


def migrate_seed(engine):
    """Explicit metadata DDL; never sends, enables or acknowledges anything."""
    from .generation_repository import metadata as generation_metadata
    _metadata_compatible(inspect(engine), engine.dialect, tables=generation_metadata.tables.values())
    metadata.create_all(engine)
    _metadata_compatible(inspect(engine), engine.dialect, tables=metadata.tables.values())
    if engine.dialect.name == 'mysql':
        with engine.connect() as connection:
            kind = connection.execute(text("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='indicator_seed_receipt'")).scalar_one()
            if kind.upper() != 'INNODB':
                raise ValueError('Transactional seed metadata required')


def _transition(registry, generation, before, after):
    with registry.outbox.transaction() as session:
        row = session.execute(select(generations).where(generations.c.id == generation).with_for_update()).mappings().one()
        route = registry.outbox.route(session, generation)
        if row['state'] != before or route['enabled'] or route['input_topic'] != row['input_topic']:
            raise ValueError('Generation seed state changed; no automatic replay allowed')
        session.execute(generations.update().where(generations.c.id == generation).values(state=after))


def _records(folder, generation, shard):
    path = folder / shard['path']
    _safe_folder(path.parent)
    if _linked(path):
        raise ValueError('Linked seed source')
    digest = sha256(); count = size = 0
    with path.open('rb') as handle:
        while line := handle.readline(1024 * 1024 + 1):
            if len(line) > 1024 * 1024 or not line.endswith(b'\n'):
                raise ValueError('Seed source record bound exceeded')
            digest.update(line); count += 1; size += len(line)
            yield snapshot_envelope(generation, line)
    if digest.hexdigest() != shard['sha256'] or count != shard['rows'] or size != shard['bytes']:
        raise ValueError('Seed source changed while streaming')
    # Emitted only after the entire source shard was rechecked. Transport commits
    # the stock transaction only after exhausting this generator.
    yield seed_end(generation, shard['code'])


def publish_seed(registry, generation, transport, *, progress=None):
    identity(generation)
    binding = registry.get(generation)
    require_binding(binding)
    folder = registry.snapshot_root / generation
    manifest = verify_snapshot(folder, expected_sha256=binding['manifest_sha256'])
    if manifest['stocks'] == 0:
        raise ValueError('An empty generation cannot be seeded')
    _transition(registry, generation, 'STAGED', 'SEEDING')
    try:
        transport.check(binding)
        for index, shard in enumerate(manifest['shards']):
            receipt = transport.send_stock(binding, shard['code'], _records(folder, generation, shard))
            if receipt['partition'] != partition_for(shard['code'], binding['partitions']):
                raise ValueError('Seed broker partition mismatch')
            integer(receipt['offset'], 0, 2**63-1)
            with registry.outbox.transaction() as session:
                row = session.execute(select(generations).where(generations.c.id == generation).with_for_update()).mappings().one()
                route = registry.outbox.route(session, generation)
                if row['state'] != 'SEEDING' or route['enabled']:
                    raise ValueError('Seed ownership/state changed')
                session.execute(receipts.insert().values(generation=generation, ts_code=shard['code'],
                    partition=receipt['partition'], end_offset=receipt['offset'],
                    source_rows=shard['rows'], source_sha256=shard['sha256']))
            if progress:
                progress(index + 1, manifest['stocks'])
        _transition(registry, generation, 'SEEDING', 'SEEDED')
    except Exception:
        # A process crash can leave SEEDING; that also rejects a second attempt.
        _transition(registry, generation, 'SEEDING', 'SEED_FAILED')
        raise


class KafkaSeedTransport:
    """Bounded per-stock Kafka transactions with explicit delivery receipts."""
    transaction_prefix = 'indicator-seed'
    def __init__(self, config, generation):
        from confluent_kafka import Producer
        self.generation = identity(generation)
        settings = dict(config)
        settings.update({'transactional.id': f'{self.transaction_prefix}-{generation}',
            'enable.idempotence': True, 'acks': 'all', 'transaction.timeout.ms': 120000,
            'delivery.timeout.ms': 45000, 'max.in.flight.requests.per.connection': 5})
        self.client = Producer(settings)
        self.checked = None

    def check(self, binding):
        require_binding(binding)
        if binding['id'] != self.generation:
            raise ValueError('Seed producer generation mismatch')
        metadata = self.client.list_topics(timeout=15)
        for name in (binding['input_topic'], binding['output_topic']):
            topic = metadata.topics.get(name)
            if topic is None or topic.error or set(topic.partitions) != set(range(binding['partitions'])):
                raise ValueError('Generation topic partition metadata mismatch')
        self.client.init_transactions(30)
        self.checked = (binding['id'], binding['input_topic'], binding['partitions'])

    def send_stock(self, binding, code, records):
        if self.checked != (binding['id'], binding['input_topic'], binding['partitions']):
            raise ValueError('Seed producer not checked for this binding')
        partition = partition_for(code, binding['partitions'])
        deadline = time.monotonic() + 60
        acknowledgements = []; failures = []
        def delivered(error, message):
            if error is not None:
                failures.append(True)
            elif message.topic() != binding['input_topic'] or message.partition() != partition:
                failures.append(True)
            else:
                acknowledgements.append(message.offset())
        self.client.begin_transaction()
        try:
            count = 0
            for payload in records:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Seed stock transaction timed out')
                    try:
                        self.client.produce(binding['input_topic'], key=code.encode(), value=payload,
                                            partition=partition, on_delivery=delivered)
                        break
                    except BufferError:
                        self.client.poll(.05)
                count += 1
                self.client.poll(0)
            remaining = max(0, deadline-time.monotonic())
            if self.client.flush(remaining) or failures or len(acknowledgements) != count or count == 0:
                raise RuntimeError('Seed delivery not confirmed')
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Seed stock transaction timed out')
            self.client.commit_transaction(remaining)
            return dict(partition=partition, offset=max(acknowledgements))
        except Exception:
            self.client.abort_transaction(10)
            raise
