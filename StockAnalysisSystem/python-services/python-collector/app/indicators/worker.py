"""Single-owner indicator tail and nightly CURRENT verification.

This worker never migrates schemas, trains, resets offsets or replays a seed.
New membership and historical corrections require an isolated replacement.
Local status is advisory: only immutable database proofs authorize reads.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

from sqlalchemy import text

from .outbox import identity, integer


class ProjectionWorker:
    def __init__(self, generation, publisher, lifecycle, *, inspect, latest,
                 raw_ready, available_gib, assert_owner, batch_size=500):
        self.generation=identity(generation)
        self.publisher=publisher
        self.lifecycle=lifecycle
        self.inspect=inspect
        self.latest=latest
        self.raw_ready=raw_ready
        self.available_gib=available_gib
        self.assert_owner=assert_owner
        self.batch_size=integer(batch_size,1,5000)

    def tick(self):
        self.assert_owner()
        before=self.inspect()
        if before['generation']!=self.generation:return dict(status='BINDING_CHANGED')
        if self.available_gib()<8:return dict(status='WAITING_MEMORY')
        target=self.latest()
        if before['replacement_required']:return dict(status='REPLACEMENT_REQUIRED')
        if before['cutoff']>=target and not before['pending']:return dict(status='IDLE',verified_through=before['cutoff'])
        if not self.raw_ready(target):return dict(status='WAITING_SOURCE',target=target)
        delivered=0
        for _ in range(self.batch_size):
            self.assert_owner()
            if not self.publisher.run_once():break
            delivered+=1
        self.assert_owner()
        after=self.inspect()
        if after['generation']!=self.generation or after['epoch']!=before['epoch']:
            return dict(status='BINDING_CHANGED',delivered=delivered)
        if after['replacement_required']:return dict(status='REPLACEMENT_REQUIRED',delivered=delivered)
        if after['pending']:return dict(status='WAITING_DELIVERY',delivered=delivered)
        # ACK is not projection/checkpoint coverage. The real lifecycle audit
        # may fail transiently until Flink and ClickHouse catch up; no advance.
        result=self.lifecycle.refresh(expected_epoch=before['epoch'],expected_generation=self.generation,expected_cutoff=target)
        self.assert_owner()
        return dict(status='VERIFIED',delivered=delivered,**result)


def inspect_active(engine):
    """Read only. No max-ID watermark substitutes for exact delivery coverage."""
    from .capture import source_scope
    with engine.connect() as c:
        row=c.execute(text("SELECT a.generation,a.epoch,p.cutoff,p.evidence_json FROM indicator_projection_active a "
            "JOIN indicator_projection_proof p ON p.id=a.proof_id AND p.generation=a.generation "
            "AND p.kind='CURRENT' WHERE a.slot='daily'")).mappings().one()
        g=identity(row['generation']);proof=json.loads(row['evidence_json'])
        offsets=proof['required_offsets']
        if type(offsets) is not list or not offsets:raise ValueError('Current receipt boundaries required')
        for offset in offsets:integer(offset,-1,2**63-2)
        # This check is a scheduling hint, not the authority audit. Full source
        # membership and all revisions are rechecked under the source fence.
        new_code=c.execute(text(f"SELECT 1 FROM stock_daily d WHERE {source_scope('mysql','d')} "
            "AND NOT EXISTS (SELECT 1 FROM indicator_seed_receipt s WHERE s.generation=:g AND s.ts_code=d.ts_code) LIMIT 1"),dict(g=g)).first()
        pending=c.execute(text("SELECT 1 FROM indicator_outbox o LEFT JOIN indicator_outbox_delivery d "
            "ON d.outbox_id=o.id AND d.route_id=:g WHERE d.state IS NULL OR d.state<>'ACKED' LIMIT 1"),dict(g=g)).first()
        bounds=' OR '.join(f'(d.broker_partition={p} AND d.broker_offset>{offset})' for p,offset in enumerate(offsets))
        old_change=c.execute(text("SELECT 1 FROM indicator_outbox o LEFT JOIN indicator_outbox_delivery d "
            "ON d.outbox_id=o.id AND d.route_id=:g WHERE o.trade_date<=:cutoff AND "
            "(d.state IS NULL OR d.state<>'ACKED' OR "+bounds+") LIMIT 1"),dict(g=g,cutoff=row['cutoff'])).first()
        return dict(generation=g,epoch=row['epoch'],cutoff=row['cutoff'],pending=bool(pending),
            replacement_required=bool(new_code or old_change))


def _save(folder, result):
    from .snapshot import _safe_folder, _linked
    folder=_safe_folder(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'worker.state.json'
    if _linked(path):raise ValueError('Linked worker status rejected')
    temporary=folder/(uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as f:
            json.dump(dict(result,observed_at=datetime.now(timezone.utc).isoformat(),authority_status_only=True),f,allow_nan=False)
            f.flush();os.fsync(f.fileno())
        os.replace(temporary,path)
    finally:
        temporary.unlink(missing_ok=True)


class _RefreshCommand:
    def __init__(self,args,project,env,job_id):
        self.args=args;self.project=project;self.env=env;self.job_id=job_id

    def refresh(self, *, expected_epoch, expected_generation, expected_cutoff):
        command=[sys.executable,'-m','app.indicators']
        if self.args.snapshot_root:command+=['--snapshot-root',self.args.snapshot_root]
        if self.args.calendar_root:command+=['--calendar-root',self.args.calendar_root]
        command+=['refresh','--job-id',self.job_id,'--expected-epoch',str(expected_epoch),
                  '--expected-generation',expected_generation,'--source-day',expected_cutoff]
        result=subprocess.run(command,cwd=self.project/'python-services/python-collector',env=self.env,
            capture_output=True,text=True,timeout=10800,check=False)
        if result.returncode!=0:raise RuntimeError('Current projection audit not accepted')
        value=json.loads(result.stdout.splitlines()[-1])
        if value.get('generation')!=expected_generation or value.get('epoch')!=expected_epoch+1:
            raise ValueError('Current projection receipt changed')
        return value


def successful_source_day(factory,day):
    from app.repositories.market_update_repository import MarketUpdateRepository
    runs=MarketUpdateRepository(factory).list_runs(day,day)
    return bool(runs) and all(r['status']=='SUCCEEDED' for r in runs)


def run(args,db,project,env):
    """Explicit command only. Advisory lock is DB-scoped, not host-path scoped."""
    import psutil
    from .cli import _root, _required
    from .generation_repository import Generations
    from .snapshot import _linked
    from .tail import KafkaTailTransport, TailPublisher
    from .readers import ClickHouseProjectionReader
    from app.preparation.calendar import CalendarStore
    from .source_guard import completed_cutoff
    descriptor=_root(project,args.deployment_file,args.deployment_file)
    if _linked(descriptor) or descriptor.stat().st_size>65536:raise ValueError('Bounded deployment receipt required')
    raw=descriptor.read_bytes();receipt=json.loads(raw)
    g=identity(receipt['generation']);job_id=identity(receipt['job_id'])
    if receipt.get('status')!='RUNNING' or len(receipt.get('jar_sha256',''))!=64:
        raise ValueError('Operator-pinned V2 deployment receipt required')
    snapshots=_root(project,args.snapshot_root or env.get('INDICATOR_SNAPSHOT_ROOT'),'.runtime/indicator_snapshots')
    registry=Generations(db.Session,snapshots)
    binding=registry.get(g)
    if receipt.get('partitions')!=binding['partitions'] or receipt.get('manifest_sha256')!=binding['manifest_sha256']:
        raise ValueError('Deployment generation binding changed')
    calendar_root=_root(project,args.calendar_root or env.get('PREPARATION_DATA_ROOT'),'.runtime/stock_pool_preparation')
    calendar=CalendarStore(calendar_root,None,None)
    transport=KafkaTailTransport({'bootstrap.servers':_required(env,'KAFKA_BOOTSTRAP_SERVERS')},g)
    reader=ClickHouseProjectionReader(_required(env,'CLICKHOUSE_HTTP_URL'),_required(env,'CLICKHOUSE_DB'),
        _required(env,'CLICKHOUSE_USER'),_required(env,'CLICKHOUSE_PASSWORD'),None)
    def inspect():
        result=inspect_active(db.engine)
        controls=reader.controls(binding)
        try:
            if any(row['kind']!='SEED_COMPLETE' for row in controls):result['replacement_required']=True
        finally:
            controls.close()
        return result
    _required(env,'FLINK_REST_URL')
    with db.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as lock:
        database=lock.exec_driver_sql('SELECT DATABASE()').scalar_one()
        name='indicator-worker:'+sha256(database.encode()).hexdigest()[:40]
        if lock.execute(text('SELECT GET_LOCK(:name,0)'),dict(name=name)).scalar_one()!=1:
            raise RuntimeError('Indicator worker already owned')
        connection_id=lock.exec_driver_sql('SELECT CONNECTION_ID()').scalar_one()
        def assert_owner():
            if (lock.invalidated or lock.closed or lock.execute(text('SELECT IS_USED_LOCK(:name)'),dict(name=name)).scalar_one()!=connection_id
                    or descriptor.read_bytes()!=raw):
                raise RuntimeError('Indicator worker ownership or deployment changed')
        try:
            worker=ProjectionWorker(g,TailPublisher(registry,g,transport),_RefreshCommand(args,project,dict(env),job_id),
                inspect=inspect,
                latest=lambda:completed_cutoff(calendar.cached(),datetime.now(timezone.utc)),
                raw_ready=lambda day:successful_source_day(db.Session,day),available_gib=lambda:psutil.virtual_memory().available/2**30,assert_owner=assert_owner)
            while True:
                assert_owner()
                try:result=worker.tick()
                except Exception:
                    # Recheck ownership separately; never silently reacquire.
                    assert_owner()
                    result=dict(status='WAITING_DEPENDENCIES',indicator_ready=False)
                _save(descriptor.parent,dict(result,generation=g,job_id=job_id))
                if args.once:return result
                if result['status']=='BINDING_CHANGED':raise RuntimeError('Pinned deployment changed')
                time.sleep(args.poll_seconds)
        finally:
            reader.close()
            lock.execute(text('SELECT RELEASE_LOCK(:name)'),dict(name=name))
