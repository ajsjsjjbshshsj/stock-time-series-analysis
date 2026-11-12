"""Bounded outbox delivery after verified bootstrap; never enables a route.

Each call processes one per-stock head. A broker failure leaves its durable
lease retryable; a successful transaction is acknowledged with the exact
fence/partition/offset. This is at-least-once across MySQL and Kafka, not a
distributed transaction. New stocks require explicit bootstrap before delivery.
"""
from uuid import uuid4
from sqlalchemy import select

from .outbox import identity
from .seed import KafkaSeedTransport, receipts
from .wire import require_binding, mutation_envelope, partition_for


class KafkaTailTransport(KafkaSeedTransport):
    # A stable ID fences an earlier tail producer for this generation without
    # fencing the seed producer, which has a different lifecycle and namespace.
    transaction_prefix = 'indicator-tail'


class TailPublisher:
    def __init__(self, registry, generation, transport):
        self.registry = registry
        self.generation = identity(generation)
        self.transport = transport
        self.owner = uuid4().hex
        self.checked = False

    def run_once(self):
        binding = self.registry.get(self.generation)
        require_binding(binding)
        if binding['state'] not in ('VERIFIED', 'ACTIVE'):
            raise ValueError('Verified indicator seed required before tail delivery')
        with self.registry.outbox.transaction() as session:
            route = self.registry.outbox.route(session, self.generation)
            if not route['enabled'] or route['input_topic'] != binding['input_topic']:
                raise ValueError('Verified indicator route is not enabled')
        if not self.checked:
            self.transport.check(binding)
            self.checked = True
        self.registry.outbox.enroll(self.generation, limit=1000)
        claimed = self.registry.outbox.claim(self.generation, self.owner, limit=1, lease_seconds=180)
        if not claimed:
            return 0
        event = claimed[0]
        with self.registry.factory() as session:
            seeded = session.execute(select(receipts.c.ts_code).where(
                receipts.c.generation == self.generation, receipts.c.ts_code == event['ts_code'])).scalar_one_or_none()
        if seeded is None:
            raise ValueError('New stock requires verified bootstrap before tail delivery')
        payload = mutation_envelope(self.generation, event)
        receipt = self.transport.send_stock(binding, event['ts_code'], iter((payload,)))
        if receipt['partition'] != partition_for(event['ts_code'], binding['partitions']):
            raise ValueError('Tail broker partition mismatch')
        self.registry.outbox.ack(self.generation, event['id'], self.owner, event['fence'],
            topic=binding['input_topic'], partition=receipt['partition'], offset=receipt['offset'])
        return 1
