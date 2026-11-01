"""Verified seed coverage, conservative current-tail cutover and safe rollback.

No constructor/startup DDL and no network defaults. Explicit migration installs
an empty active pointer (legacy reads remain unchanged). SEED evidence proves
only sealed historical coverage. Only fresh CURRENT evidence obtained inside a
live SourceGuard and locked route can authorize pointer CAS. Proofs are inserted
once; no update/delete/repair-clear method exists. Retain all generations and
checkpoints. Infrastructure readers and fence implementation need native tests
before this authority is wired into an operational command.
"""
from hashlib import sha256
from datetime import date
import time
from uuid import uuid4

from sqlalchemy import (MetaData, Table, Column, String, Text, Integer, BigInteger,
                        inspect, select, text)
from sqlalchemy.dialects.mysql import LONGTEXT

from .capture import _metadata_compatible, require_capture
from .generation_repository import generations, metadata as generation_metadata
from .outbox import deliveries, identity, integer, outbox, routes
from .projection_verifier import (ProjectionReader, SourceGuard, canonical,
                                  validate_checkpoint, verify_projection, Checkpoint)
from .seed import receipts, metadata as seed_metadata
from .snapshot import verify_snapshot
from .wire import partition_for


metadata = MetaData()
proofs = Table('indicator_projection_proof', metadata,
    Column('id', String(32), primary_key=True),
    Column('generation', String(32), nullable=False),
    Column('kind', String(8), nullable=False),
    Column('manifest_sha256', String(64), nullable=False),
    Column('source_sha256', String(64), nullable=False),
    Column('cutoff', String(10), nullable=False),
    Column('source_rows', BigInteger, nullable=False),
    Column('source_stocks', Integer, nullable=False),
    Column('checkpoint_job_id', String(32), nullable=False),
    Column('checkpoint_id', BigInteger, nullable=False),
    Column('checkpoint_ref', String(160), nullable=False),
    Column('fence_token', String(32)),
    Column('evidence_json', Text().with_variant(LONGTEXT(), 'mysql'), nullable=False),
    Column('evidence_sha256', String(64), nullable=False),
    Column('created_at', BigInteger, nullable=False), mysql_engine='InnoDB')
coverage = Table('indicator_projection_coverage', metadata,
    Column('generation', String(32), primary_key=True),
    Column('proof_id', String(32), nullable=False),
    Column('acked_rows', BigInteger, nullable=False),
    Column('byte_offset', BigInteger, nullable=False), mysql_engine='InnoDB')
active = Table('indicator_projection_active', metadata,
    Column('slot', String(16), primary_key=True),
    Column('generation', String(32)),
    Column('previous_generation', String(32)),
    Column('epoch', BigInteger, nullable=False),
    Column('proof_id', String(32)),
    Column('switched_at', BigInteger), mysql_engine='InnoDB')


def migrate_projection(engine):
    """Explicit additive DDL, compatible re-entry; never activates or sends."""
    for required in (generation_metadata, seed_metadata):
        _metadata_compatible(inspect(engine), engine.dialect, tables=required.tables.values())
    metadata.create_all(engine)
    _metadata_compatible(inspect(engine), engine.dialect, tables=metadata.tables.values())
    _immutable_proofs(engine)
    with engine.begin() as connection:
        if engine.dialect.name == 'mysql':
            for name in metadata.tables:
                kind = connection.execute(text('SELECT ENGINE FROM information_schema.TABLES '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name'), dict(name=name)).scalar_one()
                if kind.upper() != 'INNODB':
                    raise ValueError('Transactional projection metadata required')
        if connection.execute(select(active.c.slot).where(active.c.slot == 'daily')).first() is None:
            connection.execute(active.insert().values(slot='daily',epoch=0))


def _immutable_proofs(engine):
    """Reject UPDATE/DELETE at the database boundary, including accidental SQL."""
    if engine.dialect.name not in ('mysql','sqlite'):
        raise ValueError('Unsupported projection metadata database')
    with engine.begin() as connection:
        for operation in ('UPDATE','DELETE'):
            name = 'indicator_projection_proof_no_' + operation.lower() + '_v1'
            if engine.dialect.name=='sqlite':
                statement = (f'CREATE TRIGGER {name} BEFORE {operation} ON indicator_projection_proof '
                    "FOR EACH ROW BEGIN SELECT RAISE(ABORT, 'Immutable projection proof'); END")
                prior = connection.execute(text("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=:name"),
                    dict(name=name)).scalar_one_or_none()
                if prior is not None and prior != statement:
                    raise ValueError('Conflicting immutable projection proof trigger')
            else:
                body = "SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Immutable projection proof'"
                statement = f'CREATE TRIGGER {name} BEFORE {operation} ON indicator_projection_proof FOR EACH ROW {body}'
                prior = connection.execute(text('SELECT EVENT_MANIPULATION,ACTION_TIMING,EVENT_OBJECT_TABLE,ACTION_STATEMENT '
                    'FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE() AND TRIGGER_NAME=:name'),
                    dict(name=name)).mappings().one_or_none()
                if prior is not None and (prior['EVENT_MANIPULATION'] != operation or prior['ACTION_TIMING'] != 'BEFORE'
                        or prior['EVENT_OBJECT_TABLE'] != 'indicator_projection_proof' or prior['ACTION_STATEMENT'] != body):
                    raise ValueError('Conflicting immutable projection proof trigger')
            if prior is None:
                connection.exec_driver_sql(statement)


class Lifecycle:
    def __init__(self, registry, reader: ProjectionReader, guard: SourceGuard, *, clock=None, max_age=300):
        self.registry = registry
        self.reader = reader
        self.guard = guard
        self.clock = clock or time.time
        self.max_age = integer(max_age, 1, 3600)

    def _source(self, generation):
        binding = self.registry.get(generation, verify=True)
        folder = self.registry.snapshot_root / generation
        manifest = verify_snapshot(folder, expected_sha256=binding['manifest_sha256'])
        return binding, manifest, folder

    @staticmethod
    def _receipts(session, generation):
        return [dict(r) for r in session.execute(select(receipts).where(
            receipts.c.generation == generation).order_by(receipts.c.ts_code)).mappings()]

    def _locked(self, session, binding, states, *, enabled):
        row = session.execute(select(generations).where(generations.c.id == binding['id'])
            .with_for_update()).mappings().one()
        route = self.registry.outbox.route(session, binding['id'])
        if (row['state'] not in states or any(row[k] != v for k,v in binding.items() if k != 'state')
                or bool(route['enabled']) != enabled or route['input_topic'] != binding['input_topic']):
            raise ValueError('Projection generation/route changed')
        return row

    def _save(self, session, evidence):
        encoded = canonical(evidence)
        result = dict(id=uuid4().hex, generation=evidence['generation'], kind=evidence['kind'],
            manifest_sha256=evidence['manifest_sha256'],source_sha256=evidence['source_sha256'],
            cutoff=evidence['cutoff'],source_rows=evidence['source_rows'],source_stocks=evidence['source_stocks'],
            checkpoint_job_id=evidence['checkpoint']['job_id'],checkpoint_id=evidence['checkpoint']['checkpoint_id'],
            checkpoint_ref=evidence['checkpoint']['checkpoint_ref'],fence_token=evidence['fence_token'],
            evidence_json=encoded.decode('ascii'),evidence_sha256=sha256(encoded).hexdigest(),created_at=int(self.clock()))
        session.execute(proofs.insert().values(**result))
        return result

    def _audit(self, binding, manifest, seed_receipts, folder, *, source=None):
        return verify_projection(binding, manifest, seed_receipts, self.reader, folder,
            clock=self.clock, max_age=self.max_age, source=source)

    def _fresh(self, binding, evidence):
        cp = dict(evidence['checkpoint'])
        for key in ('next_offsets','task_attempts_before','task_attempts_after','uninitialized_partitions'):
            cp[key] = tuple(cp[key])
        validate_checkpoint(binding, Checkpoint(**cp), evidence['required_offsets'],
            now=int(self.clock()), max_age=self.max_age)

    def verify_seed(self, generation):
        binding, manifest, folder = self._source(generation)
        if binding['state'] != 'SEEDED':
            raise ValueError('Only a sealed SEEDED generation can be verified')
        with self.registry.factory() as session:
            pinned = self._receipts(session, generation)
        evidence = self._audit(binding, manifest, pinned, folder)
        with self.registry.outbox.transaction() as session:
            self._locked(session,binding,{'SEEDED'},enabled=False)
            if self._receipts(session,generation) != pinned:
                raise ValueError('Seed receipts changed during verification')
            self._fresh(binding,evidence)
            result = self._save(session,evidence)
            session.execute(coverage.insert().values(generation=generation,proof_id=result['id'],acked_rows=0,byte_offset=0))
            session.execute(generations.update().where(generations.c.id == generation).values(state='VERIFIED'))
        return result

    @staticmethod
    def _coverage(session, binding):
        progress = session.execute(select(coverage).where(coverage.c.generation == binding['id'])
            .with_for_update()).mappings().one_or_none()
        if progress is None:
            raise ValueError('Verified snapshot coverage proof missing')
        proof = session.execute(select(proofs).where(proofs.c.id == progress['proof_id'])).mappings().one_or_none()
        if (proof is None or proof['kind'] != 'SEED' or proof['generation'] != binding['id']
                or proof['manifest_sha256'] != binding['manifest_sha256']
                or sha256(proof['evidence_json'].encode('ascii')).hexdigest() != proof['evidence_sha256']):
            raise ValueError('Verified coverage evidence changed')
        return progress

    def ack_covered(self, generation, *, batch_size=1000):
        """Acknowledge ONLY explicit sealed IDs; cursor is file position, not ID."""
        integer(batch_size,1,5000)
        binding, manifest, folder = self._source(generation)
        with self.registry.outbox.transaction() as session:
            self._locked(session,binding,{'VERIFIED'},enabled=False)
            progress = self._coverage(session,binding)
            integer(progress['acked_rows'],0,manifest['covered']['rows'])
            integer(progress['byte_offset'],0,manifest['covered']['bytes'])
            count = 0
            with (folder / 'covered.jsonl').open('rb') as handle:
                handle.seek(progress['byte_offset'])
                while count < batch_size and (line := handle.readline(32)):
                    source_id = integer(int(line),1,2**63-1)
                    if line != str(source_id).encode('ascii')+b'\n':
                        raise ValueError('Covered snapshot framing changed')
                    if session.execute(select(outbox.c.id).where(outbox.c.id==source_id)).first() is None:
                        raise ValueError('Covered source event missing')
                    row = session.execute(select(deliveries).where(deliveries.c.route_id==generation,
                        deliveries.c.outbox_id==source_id).with_for_update()).mappings().one_or_none()
                    values = dict(state='ACKED',owner=None,lease_until=None,broker_partition=None,
                                  broker_offset=None,acked_at=int(self.clock()))
                    if row is None:
                        session.execute(deliveries.insert().values(route_id=generation,outbox_id=source_id,
                            fence=0,attempts=0,**values))
                    elif row['state']=='PENDING' and row['attempts']==0 and row['fence']==0:
                        session.execute(deliveries.update().where(deliveries.c.route_id==generation,
                            deliveries.c.outbox_id==source_id).values(**values))
                    else:
                        raise ValueError('Covered delivery already attempted outside seed authority')
                    count += 1
                offset = handle.tell()
            total = progress['acked_rows']+count
            complete = total==manifest['covered']['rows']
            if total > manifest['covered']['rows'] or (complete != (offset==manifest['covered']['bytes'])):
                raise ValueError('Covered snapshot cursor mismatch')
            verify_snapshot(folder, expected_sha256=binding['manifest_sha256'])
            session.execute(coverage.update().where(coverage.c.generation==generation)
                .values(acked_rows=total,byte_offset=offset))
            return dict(acked=count,complete=complete)

    def enable_tail(self, generation):
        binding, manifest, folder = self._source(generation)
        with self.registry.factory() as session:
            pinned = self._receipts(session,generation)
        evidence = self._audit(binding,manifest,pinned,folder)
        with self.registry.outbox.transaction() as session:
            self._locked(session,binding,{'VERIFIED'},enabled=False)
            progress = self._coverage(session,binding)
            if (progress['acked_rows'] != manifest['covered']['rows']
                    or progress['byte_offset'] != manifest['covered']['bytes']
                    or pinned != self._receipts(session,generation)):
                raise ValueError('Seed coverage acknowledgement incomplete or changed')
            require_capture(session.connection())
            self._fresh(binding,evidence)
            self._save(session,evidence)
            session.execute(routes.update().where(routes.c.id==generation).values(enabled=True))

    @staticmethod
    def _caught_up(session, generation):
        linked = select(deliveries.c.outbox_id).where(deliveries.c.route_id==generation,
            deliveries.c.outbox_id==outbox.c.id,deliveries.c.state=='ACKED').exists()
        # No maximum-ID cursor: every visible source event must be enrolled and ACKED.
        if session.execute(select(outbox.c.id).where(~linked).limit(1)).first() is not None:
            raise ValueError('SOURCE_NOT_QUIESCENT: unresolved or unenrolled source event')
        if session.execute(select(deliveries.c.outbox_id).where(deliveries.c.route_id==generation,
                deliveries.c.state!='ACKED').limit(1)).first() is not None:
            raise ValueError('SOURCE_NOT_QUIESCENT: pending/in-flight delivery')

    def activate(self, generation, *, expected_epoch, expected_generation):
        return self._switch(generation,expected_epoch,expected_generation,rollback=False)

    def refresh(self, *, expected_epoch, expected_generation, expected_cutoff=None, source_ready=None):
        """Advance an active generation's cutoff only after a fresh current audit.

        Ordinary nightly append does not create another generation. Keep the
        rollback target intact while replacing its verification proof by CAS.
        """
        identity(expected_generation)
        if expected_cutoff is not None:
            if date.fromisoformat(expected_cutoff).isoformat()!=expected_cutoff or not callable(source_ready):
                raise ValueError('Pinned successful source day required')
        elif source_ready is not None:
            raise ValueError('Pinned successful source day required')
        return self._switch(expected_generation,expected_epoch,expected_generation,
            rollback=False,refresh=True,expected_cutoff=expected_cutoff,source_ready=source_ready)

    def rollback(self, *, expected_epoch, expected_generation):
        with self.registry.factory() as session:
            pointer = session.execute(select(active).where(active.c.slot=='daily')).mappings().one()
        if pointer['previous_generation'] is None:
            raise ValueError('No retained generation rollback target; legacy switch is manual')
        return self._switch(pointer['previous_generation'],expected_epoch,expected_generation,rollback=True)

    @staticmethod
    def _delivery_proof(session, binding, folder, checkpoint):
        """Bounded merge against explicit seed IDs; never infer null-ACK coverage."""
        with (folder/'covered.jsonl').open('rb') as handle:
            def next_covered():
                line = handle.readline(32)
                return int(line) if line else None
            covered_id = next_covered()
            query = select(deliveries,outbox.c.ts_code).outerjoin(outbox,deliveries.c.outbox_id==outbox.c.id).where(
                deliveries.c.route_id==binding['id']).order_by(deliveries.c.outbox_id).execution_options(yield_per=500)
            result = session.execute(query)
            try:
                for delivery in result.mappings():
                    event_id = delivery['outbox_id']
                    if delivery['ts_code'] is None or (covered_id is not None and covered_id < event_id):
                        raise ValueError('Source/covered delivery missing')
                    partition, offset = delivery['broker_partition'],delivery['broker_offset']
                    if event_id == covered_id:
                        if partition is not None or offset is not None or delivery['attempts']!=0 or delivery['fence']!=0:
                            raise ValueError('Seed covered delivery proof changed')
                        covered_id = next_covered()
                    else:
                        integer(partition,0,binding['partitions']-1);integer(offset,0,2**63-1)
                        if (partition != partition_for(delivery['ts_code'],binding['partitions'])
                                or checkpoint['next_offsets'][partition]<=offset):
                            raise ValueError('Checkpoint does not cover acknowledged tail receipt')
                if covered_id is not None:
                    raise ValueError('Covered delivery missing')
            finally:
                result.close()

    def _switch(self, generation, expected_epoch, expected_generation, *, rollback, refresh=False,
                expected_cutoff=None, source_ready=None):
        identity(generation);integer(expected_epoch,0,2**63-2)
        if expected_generation is not None: identity(expected_generation)
        if generation==expected_generation and not refresh:
            raise ValueError('Generation is already active')
        binding, manifest, folder = self._source(generation)
        # Lock order: external source fence -> singleton pointer -> target
        # generation -> route -> coverage. Keep fence through metadata commit.
        with self.guard.hold(binding) as fence:
            fence.assert_held()
            def check_source_day():
                if expected_cutoff is not None and (fence.cutoff!=expected_cutoff or not source_ready(expected_cutoff)):
                    raise ValueError('Scheduled source day changed or incomplete')
            check_source_day()
            with self.registry.outbox.transaction() as session:
                pointer = session.execute(select(active).where(active.c.slot=='daily').with_for_update()).mappings().one()
                if (pointer['epoch']!=expected_epoch or pointer['generation']!=expected_generation
                        or (rollback and pointer['previous_generation']!=generation)):
                    raise ValueError('Active generation compare-and-swap failed')
                self._locked(session,binding,{'ACTIVE'} if refresh else {'VERIFIED'},enabled=True)
                require_capture(session.connection())
                progress = self._coverage(session,binding)
                if progress['acked_rows']!=manifest['covered']['rows'] or progress['byte_offset']!=manifest['covered']['bytes']:
                    raise ValueError('Snapshot coverage incomplete')
                self._caught_up(session,generation)
                evidence = self._audit(binding,manifest,self._receipts(session,generation),folder,source=fence)
                self._delivery_proof(session,binding,folder,evidence['checkpoint'])
                self._caught_up(session,generation)
                self._fresh(binding,evidence);fence.assert_held()
                check_source_day()
                proof = self._save(session,evidence)
                result = dict(generation=generation,previous_generation=pointer['previous_generation'] if refresh else expected_generation,
                    epoch=expected_epoch+1,proof_id=proof['id'],switched_at=int(self.clock()))
                changed = session.execute(active.update().where(active.c.slot=='daily',active.c.epoch==expected_epoch)
                    .values(**result))
                if changed.rowcount!=1:
                    raise ValueError('Active generation compare-and-swap failed')
                if expected_generation is not None and not refresh:
                    changed = session.execute(generations.update().where(generations.c.id==expected_generation,
                        generations.c.state=='ACTIVE').values(state='VERIFIED'))
                    if changed.rowcount!=1:
                        raise ValueError('Previous generation state changed')
                session.execute(generations.update().where(generations.c.id==generation).values(state='ACTIVE'))
                fence.assert_held()
            return result
