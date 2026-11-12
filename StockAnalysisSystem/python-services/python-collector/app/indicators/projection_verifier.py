"""Read-adapter contract and bounded, independent projection proof.

Adapters are trusted infrastructure, NOT caller-supplied readiness flags. A
ProjectionReader must read both ClickHouse tables with FINAL, without LIMIT,
under the bound generation, retaining every control (especially repairs).
Checkpoint next_offsets are conservative lower bounds for the actual completed
checkpoint, never consumer-group offsets, broker end offsets or prefetched
positions. The native adapter samples the current job's source-local
KafkaSourceReader committedOffset gauges (NEXT offsets), then waits for a
strictly later-triggered completed checkpoint in the SAME execution attempts.
With commit.offsets.on.checkpoint=true, this gauge is updated by this reader's
successful notifyCheckpointComplete commit callback from its snapshotState
offsetsToCommit, NOT by reading a broker consumer-group offset. Native acceptance
must confirm that provenance in the actual connector version. Never substitute
currentOffset (pre-emit) or broker-end/group offsets. A PRESENT gauge at -1 may
contribute only the trivial lower bound zero, explicitly listed in
uninitialized_partitions; that partition must have NO required source/control
receipt. A missing gauge is never such evidence and must fail in the adapter.
Pin all task attempts/restarts around sampling and completion, reject ambiguous
or missing partition metrics, and verify actual topology/config uses this exact
generation's topics/group/UIDs, read_committed and EXACTLY_ONCE checkpoints/sink.
These are adapter obligations, not conclusions that a JSON shape can establish.

SourceGuard.hold must drain existing source writers and exclude new writes for
the whole context, including the final metadata commit. On MySQL, a dedicated
connection's LOCK TABLES stock_daily READ is one possible operational boundary;
native acceptance must establish its trigger/table-lock behavior. GET_LOCK or
an application pause alone does not fence legacy/admin writers. Its SourceFence
reads authoritative stock_daily and revisions from a NEW snapshot after fencing,
through the latest completed calendar cutoff. Every code, date and revision is
required; no caches or the generation's old seed snapshot may substitute for it.
The lifecycle additionally locks the route and rejects in-flight leases. Guard
loss must raise and the guard MUST remain held until SQL commit finishes.
"""
from dataclasses import asdict, dataclass
from hashlib import sha256
import re
from typing import ContextManager, Iterable, Mapping, Protocol

import simplejson

from .audit import audit_stock
from .outbox import identity, integer
from .snapshot import _date, verify_snapshot
from .wire import _code, _loads, partition_for, require_binding


@dataclass(frozen=True)
class Checkpoint:
    generation: str
    job_id: str
    job_name: str
    job_state: str
    checkpoint_id: int
    latest_checkpoint_id: int
    status: str
    checkpoint_ref: str  # Portable: indicator-v2/<generation>/<job-id>/chk-<id>.
    input_topic: str
    output_topic: str
    next_offsets: tuple[int, ...]  # Proven CP lower bounds, indexed by partition.
    started_at: int  # Epoch seconds, from Flink, not observer wall time.
    completed_at: int
    offset_proof: str  # Exactly SOURCE_CHECKPOINT_COMMIT_METRIC_BEFORE_BARRIER.
    offsets_observed_at: int  # END of metric sampling; epoch seconds.
    offsets_confirmed_at: int  # Attempts/job rechecked AFTER checkpoint completes.
    observed_job_id: str
    confirmed_job_id: str
    # REST-observable fingerprints, NOT nonexistent Flink REST attempt UUIDs:
    # vertexId:subtask:attemptNumber:startTimeMillis:taskmanagerId (split(':',4)).
    task_attempts_before: tuple[str, ...]
    task_attempts_after: tuple[str, ...]  # ALL vertices/subtasks, not only sources.
    restarts_before: int
    restarts_after: int
    uninitialized_partitions: tuple[int, ...] = ()


class ProjectionReader(Protocol):
    def codes(self, binding: Mapping) -> Iterable[str]: ...
    def rows(self, binding: Mapping, code: str) -> Iterable[Mapping]: ...
    def controls(self, binding: Mapping) -> Iterable[Mapping]: ...
    def checkpoint(self, binding: Mapping) -> Checkpoint: ...


class SourceFence(Protocol):
    token: str  # Unique lowercase 32-hex operation token, not a secret.
    cutoff: str
    def assert_held(self) -> None: ...
    def codes(self) -> Iterable[str]: ...
    def rows(self, generation: str, code: str) -> Iterable[Mapping]: ...


class SourceGuard(Protocol):
    def hold(self, binding: Mapping) -> ContextManager[SourceFence]: ...


def canonical(value):
    return simplejson.dumps(value, sort_keys=True, use_decimal=True,
        allow_nan=False, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def _codes(values):
    result = []
    for code in values:
        _code(code)
        if len(result) >= 10000 or (result and code <= result[-1]):
            raise ValueError('Projection code ordering/count mismatch')
        result.append(code)
    if not result:
        raise ValueError('Empty projection cannot prove readiness')
    return result


def _provenance(binding, row, *, control=False):
    code = _code(row['ts_code'])
    partition = partition_for(code, binding['partitions'])
    if (row['generation'] != binding['id'] or integer(row['schema_version'],1,2) != (2 if control else 1)
            or row['input_topic'] != binding['input_topic']
            or row['kafka_topic'] != binding['output_topic']
            or integer(row['input_partition'], 0, 255) != partition
            or integer(row['kafka_partition'], 0, 255) != partition):
        raise ValueError('Projection transport provenance mismatch')
    incoming = integer(row['input_offset'], 0, 2**63-1)
    outgoing = integer(row['kafka_offset'], 0, 2**63-1)
    if integer(row['version'], 0, 2**63-1) != outgoing:
        raise ValueError('Projection output version mismatch')
    return partition, incoming, outgoing


def validate_checkpoint(binding, checkpoint, required_offsets, *, now, max_age):
    if not isinstance(checkpoint, Checkpoint):
        raise ValueError('Typed completed checkpoint evidence required')
    identity(checkpoint.job_id)
    integer(checkpoint.checkpoint_id, 1, 2**63-1)
    integer(checkpoint.latest_checkpoint_id, 1, 2**63-1)
    integer(checkpoint.started_at, 0, 2**63-1)
    integer(checkpoint.completed_at, 0, 2**63-1)
    integer(checkpoint.offsets_observed_at,0,2**63-1)
    integer(checkpoint.offsets_confirmed_at,0,2**63-1)
    integer(checkpoint.restarts_before,0,2**63-1)
    integer(checkpoint.restarts_after,0,2**63-1)
    attempts = checkpoint.task_attempts_before
    if (type(attempts) is not tuple or not attempts or type(checkpoint.task_attempts_after) is not tuple
            or checkpoint.task_attempts_after != attempts or tuple(sorted(set(attempts))) != attempts
            or any(type(a) is not str or len(a)>512 or re.fullmatch(
                r'[0-9a-f]{32}:(?:0|[1-9][0-9]*):(?:0|[1-9][0-9]*):[1-9][0-9]*:[A-Za-z0-9._:\[\]-]+',a) is None for a in attempts)
            or len({':'.join(a.split(':')[:2]) for a in attempts}) != len(attempts)):
        raise ValueError('Missing, ambiguous or changed Flink execution attempts')
    if any(int(a.split(':',4)[3]) > checkpoint.offsets_observed_at*1000 for a in attempts):
        raise ValueError('Flink attempt started after source position observation')
    expected_ref = f'indicator-v2/{binding["id"]}/{checkpoint.job_id}/chk-{checkpoint.checkpoint_id}'
    if (checkpoint.generation != binding['id']
            or checkpoint.job_name != 'stock-generation-indicator-v2-' + binding['id']
            or checkpoint.job_state != 'RUNNING' or checkpoint.status != 'COMPLETED'
            or checkpoint.latest_checkpoint_id != checkpoint.checkpoint_id
            or checkpoint.checkpoint_ref != expected_ref
            or checkpoint.input_topic != binding['input_topic']
            or checkpoint.output_topic != binding['output_topic']
            or checkpoint.offset_proof != 'SOURCE_CHECKPOINT_COMMIT_METRIC_BEFORE_BARRIER'
            or checkpoint.observed_job_id != checkpoint.job_id or checkpoint.confirmed_job_id != checkpoint.job_id
            or checkpoint.restarts_before != checkpoint.restarts_after
            or not now - max_age <= checkpoint.offsets_observed_at < checkpoint.started_at
                <= checkpoint.completed_at <= checkpoint.offsets_confirmed_at <= now
            or type(checkpoint.next_offsets) is not tuple
            or len(checkpoint.next_offsets) != binding['partitions']):
        raise ValueError('Stale, ambiguous or incorrectly bound checkpoint')
    for partition, next_offset in enumerate(checkpoint.next_offsets):
        integer(next_offset, 0, 2**63-1)
        if next_offset <= required_offsets[partition]:
            raise ValueError('Checkpoint does not cover verified input receipts')
    idle = checkpoint.uninitialized_partitions
    if type(idle) is not tuple or tuple(sorted(set(idle))) != idle:
        raise ValueError('Invalid uninitialized partition evidence')
    for partition in idle:
        integer(partition,0,binding['partitions']-1)
        if checkpoint.next_offsets[partition] != 0 or required_offsets[partition] != -1:
            raise ValueError('Uninitialized source metric cannot cover a receipt')


def verify_projection(binding, manifest, seed_receipts, reader: ProjectionReader,
                      folder, *, clock, max_age=300, source: SourceFence | None = None):
    """Produce evidence, never mutate state; no partial/empty proof is accepted."""
    require_binding(binding)
    integer(max_age, 1, 3600)
    expected = [shard['code'] for shard in manifest['shards']]
    actual_codes = _codes(reader.codes(binding))
    if actual_codes != expected or [r['ts_code'] for r in seed_receipts] != expected:
        raise ValueError('Seed/projection stock coverage mismatch')
    if source is not None:
        source.assert_held(); identity(source.token); _date(source.cutoff)
        if _codes(source.codes()) != expected or source.cutoff < manifest['end']:
            raise ValueError('Current source membership/cutoff requires replacement generation')
    required_offsets = [-1] * binding['partitions']
    receipt_by_code = dict(zip(expected, seed_receipts))
    for shard, receipt in zip(manifest['shards'], seed_receipts):
        if (receipt['generation'] != binding['id']
                or receipt['partition'] != partition_for(shard['code'], binding['partitions'])
                or receipt['source_rows'] != shard['rows'] or receipt['source_sha256'] != shard['sha256']):
            raise ValueError('Immutable seed receipt mismatch')
        integer(receipt['end_offset'], 0, 2**63-1)

    def read_barriers():
        barriers = {}
        for control in reader.controls(binding):
            partition, incoming, outgoing = _provenance(binding, control, control=True)
            code = control['ts_code']
            receipt = receipt_by_code.get(code)
            if (control['kind'] != 'SEED_COMPLETE' or receipt is None or code in barriers
                    or control['trade_date'] is not None or control['reason'] is not None
                    or integer(control['source_revision'], 0, 2**63-1) != 0
                    or incoming != receipt['end_offset']):
                raise ValueError('Missing, duplicate, repaired or incorrect seed barrier')
            barriers[code] = dict(input_offset=incoming, output_offset=outgoing, partition=partition)
            required_offsets[partition] = max(required_offsets[partition], incoming)
        if sorted(barriers) != expected:
            raise ValueError('Incomplete seed barriers')
        return barriers

    try:
        barriers = read_barriers()

        summaries = []
        source_digest = sha256(); output_digest = sha256()
        for shard in manifest['shards']:
            code = shard['code']; barrier = barriers[code]
            def points():
                if source is None:
                    with (folder / shard['path']).open('rb') as handle:
                        yield from (_loads(line) for line in handle)
                else:
                    yield from source.rows(binding['id'], code)

            def checked_points():
                for point in points():
                    event = point['event']
                    day = _date(event['tradeDate']).isoformat()
                    if day > (source.cutoff if source is not None else manifest['end']):
                        raise ValueError('Source date exceeds proven cutoff')
                    source_digest.update(canonical(dict(generation=point['generation'],
                        revision=point['sourceRevision'], code=event['tsCode'], date=day,
                        close=event['close'], volume=event['volume'], pct_chg=event['pctChg'])) + b'\n')
                    yield point

            def checked_rows():
                prior_incoming = prior_outgoing = -1
                for row in reader.rows(binding, code):
                    partition, incoming, outgoing = _provenance(binding, row)
                    if incoming <= prior_incoming or outgoing <= prior_outgoing:
                        raise ValueError('Projection receipt order mismatch')
                    if source is None and (incoming >= barrier['input_offset'] or outgoing >= barrier['output_offset']):
                        raise ValueError('Seed indicator outside seed barrier')
                    prior_incoming, prior_outgoing = incoming, outgoing
                    required_offsets[partition] = max(required_offsets[partition], incoming)
                    fields = ('generation','ts_code','source_revision','input_topic','input_partition',
                        'input_offset','kafka_topic','kafka_partition','kafka_offset','version',
                        'close','volume','pct_chg','ma5','ma10','ma20','vol_ma5','vol_ma10',
                        'volume_ratio','window_size','is_warmup')
                    output_digest.update(canonical(dict({key: row[key] for key in fields},
                        trade_date=str(row['trade_date']))) + b'\n')
                    yield row

            summary = audit_stock(binding['id'], code, checked_points(), checked_rows())
            if source is None and any(summary[key] != shard[key] for key in ('rows','first','last')):
                raise ValueError('Seed logical coverage mismatch')
            summaries.append(summary)
        checkpoint = reader.checkpoint(binding)
        validate_checkpoint(binding, checkpoint, required_offsets, now=int(clock()), max_age=max_age)
        if read_barriers() != barriers:
            raise ValueError('Projection control evidence changed during audit')
        verify_snapshot(folder, expected_sha256=binding['manifest_sha256'])
        if source is not None:
            source.assert_held()
        return dict(generation=binding['id'],kind='CURRENT' if source else 'SEED',
            manifest_sha256=binding['manifest_sha256'],source_sha256=source_digest.hexdigest(),
            projection_sha256=output_digest.hexdigest(),cutoff=source.cutoff if source else manifest['end'],
            source_rows=sum(s['rows'] for s in summaries),source_stocks=len(summaries),
            stocks=summaries,barriers=barriers,required_offsets=required_offsets,
            checkpoint=asdict(checkpoint),fence_token=source.token if source else None)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError('Malformed projection evidence') from error
