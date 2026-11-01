"""Read-only native adapters for a known, separately deployed V2 job.

No DDL, job submission, worker recovery, broker offset reset or source fetches
occur here. REST observations do not authenticate an arbitrary job binary: the
operator must pin the deployed GenerationIndicatorJob artifact and job ID.
"""
import asyncio
import json
import math
import re
import time
from urllib.parse import urlsplit

import aiohttp

from .outbox import identity, integer
from .projection_verifier import Checkpoint, validate_checkpoint
from .wire import _code, _loads, require_binding
from ._dns import BoundedResolver


def _endpoint(value):
    uri = urlsplit(value)
    if (uri.scheme not in ('http','https') or not uri.hostname or uri.username is not None
            or uri.password is not None or uri.query or uri.fragment or uri.path not in ('','/')):
        raise ValueError('Explicit credential-free service endpoint required')
    return value.rstrip('/')


class ClickHouseProjectionReader:
    def __init__(self, endpoint, database, user, password, checkpoint_reader, *, query_timeout=120):
        self.endpoint = _endpoint(endpoint)
        if (type(database) is not str or re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,127}',database) is None
                or not user or not password):
            raise ValueError('Explicit ClickHouse configuration required')
        self.database = database
        self.auth = aiohttp.BasicAuth(user,password)
        self.query_timeout = integer(query_timeout,1,300)
        self._loop = self._client = None
        self._reading = False
        self.checkpoint_reader = checkpoint_reader

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self._loop is not None:
            try:
                if self._client is not None:
                    self._loop.run_until_complete(asyncio.wait_for(self._client.close(),2))
            finally:
                self._loop.close()
                self._loop = self._client = None

    async def _stream(self, params):
        if self._client is None:
            self._client = aiohttp.ClientSession(auth=self.auth,trust_env=False,
                timeout=aiohttp.ClientTimeout(total=self.query_timeout,connect=10,sock_read=30),
                connector=aiohttp.TCPConnector(resolver=BoundedResolver()),
                auto_decompress=False,headers={'Accept-Encoding':'identity'})
        async with self._client.post(self.endpoint,params=params,data=b'',allow_redirects=False) as response:
            if (response.status != 200 or response.headers.get('X-ClickHouse-Exception-Code','0') != '0'
                    or response.headers.get('Content-Encoding','identity') not in ('','identity')):
                raise ValueError('Indicator projection dependency unavailable')
            pending = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                pending.extend(chunk)
                while (newline := pending.find(b'\n')) >= 0:
                    if newline+1>1024*1024:
                        raise ValueError('Indicator projection response bound exceeded')
                    line = bytes(pending[:newline+1]); del pending[:newline+1]
                    if line.strip():
                        yield _loads(line)
                if len(pending)>1024*1024:
                    raise ValueError('Indicator projection response bound exceeded')
            if pending:
                raise ValueError('Indicator projection response framing invalid')

    def _rows(self, binding, sql, **parameters):
        require_binding(binding)
        params = dict(database=self.database,query=sql,param_generation=binding['id'],
            output_format_json_quote_64bit_integers='0')
        params.update({'param_'+key:value for key,value in parameters.items()})
        if self._reading:
            raise ValueError('Concurrent projection iteration is not supported')
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise ValueError('Run synchronous projection verification outside the async request loop')
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        self._reading = True
        stream = self._stream(params)
        deadline = time.monotonic()+self.query_timeout
        try:
            while True:
                remaining = deadline-time.monotonic()
                if remaining<=0:
                    raise ValueError('Indicator projection response bound exceeded')
                try:
                    row = self._loop.run_until_complete(asyncio.wait_for(anext(stream),remaining))
                except StopAsyncIteration:
                    if time.monotonic()>deadline:
                        raise ValueError('Indicator projection response bound exceeded')
                    break
                if time.monotonic()>deadline:
                    raise ValueError('Indicator projection response bound exceeded')
                yield row
        except TimeoutError:
            raise ValueError('Indicator projection response bound exceeded') from None
        except (aiohttp.ClientError,OSError):
            raise ValueError('Indicator projection dependency unavailable') from None
        finally:
            try:
                self._loop.run_until_complete(asyncio.wait_for(stream.aclose(),2))
            except Exception:
                self.close()
                raise ValueError('Indicator projection response cleanup failed') from None
            finally:
                self._reading = False

    def codes(self, binding):
        for row in self._rows(binding,'SELECT DISTINCT ts_code FROM generation_daily_indicators FINAL '
                'WHERE generation={generation:String} ORDER BY ts_code FORMAT JSONEachRow'):
            yield _code(row['ts_code'])

    def rows(self, binding, code):
        _code(code)
        return self._rows(binding,'SELECT * FROM generation_daily_indicators FINAL '
            'WHERE generation={generation:String} AND ts_code={code:String} ORDER BY trade_date FORMAT JSONEachRow',code=code)

    def controls(self, binding):
        return self._rows(binding,'SELECT * FROM generation_indicator_events FINAL '
            'WHERE generation={generation:String} ORDER BY ts_code,input_partition,input_offset,kind FORMAT JSONEachRow')

    def checkpoint(self, binding):
        if self.checkpoint_reader is None:
            raise ValueError('Native checkpoint reader required')
        return self.checkpoint_reader.checkpoint(binding)


class FlinkCheckpointReader:
    """Observe source-local completed-checkpoint positions, never group offsets.

    A strictly later completed checkpoint and unchanged ALL task identities are
    required. Empty PRESENT gauges remain explicitly uninitialized; missing
    metrics fail closed. Connector 5.0.0-2.2 updates committedOffset only from its
    own successful snapshotState/notifyCheckpointComplete callback. Deployment
    must keep commit.offsets.on.checkpoint=true and the known V2 topology.
    """
    def __init__(self, endpoint, job_id, *, session=None, clock=None, wait=None, timeout=120):
        self.endpoint = _endpoint(endpoint)
        self.job_id = identity(job_id)
        self.session = session
        self.clock = clock or time.time
        self.wait = wait or time.sleep
        self.timeout = integer(timeout,1,300)

    async def _get_native(self, suffix, parameters, budget):
        # The outer cancellation scope includes DNS/connect, headers, partial
        # bodies, EOF and cleanup; an idle-gap socket timeout is insufficient.
        async with asyncio.timeout(budget):
            async with aiohttp.ClientSession(trust_env=False,auto_decompress=False,
                    connector=aiohttp.TCPConnector(resolver=BoundedResolver()),
                    timeout=aiohttp.ClientTimeout(total=budget,connect=min(10,budget),sock_read=min(15,budget))) as session:
                async with session.get(self.endpoint+'/jobs/'+self.job_id+suffix,
                        params=parameters or None,allow_redirects=False) as response:
                    if response.status != 200:
                        raise ValueError('Flink checkpoint dependency unavailable')
                    body=bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        body.extend(chunk)
                        if len(body)>1024*1024:
                            raise ValueError('Flink checkpoint response bound exceeded')
                    return json.loads(body)

    def _get(self, suffix, **parameters):
        response = None
        try:
            if self.session is None:
                remaining=getattr(self,'_io_deadline',time.monotonic()+self.timeout)-time.monotonic()
                if remaining<=0:
                    raise ValueError('Flink checkpoint response bound exceeded')
                loop=asyncio.new_event_loop()
                try:
                    return loop.run_until_complete(self._get_native(suffix,parameters,min(15,remaining)))
                finally:
                    loop.close()
            response = self.session.get(self.endpoint+'/jobs/'+self.job_id+suffix,
                params=parameters or None,timeout=(10,15),allow_redirects=False)
            if response.status_code != 200:
                raise ValueError('Flink checkpoint dependency unavailable')
            return response.json()
        except TimeoutError:
            raise ValueError('Flink checkpoint response bound exceeded') from None
        except (aiohttp.ClientError,OSError,json.JSONDecodeError):
            raise ValueError('Flink checkpoint dependency unavailable') from None
        finally:
            if response is not None:
                response.close()

    def _identity(self, binding):
        job = self._get('')
        if (job.get('jid') != self.job_id or job.get('state') != 'RUNNING'
                or job.get('name') != 'stock-generation-indicator-v2-'+binding['id']):
            raise ValueError('Bound generation job is not running')
        config = self._get('/checkpoints/config')
        if (config.get('mode') != 'exactly_once' or config.get('externalization') != dict(enabled=True,delete_on_cancellation=False)):
            raise ValueError('Isolated retained exactly-once checkpoints required')
        vertices = job['vertices']
        if len(vertices)!=2 or len({v['id'] for v in vertices})!=2:
            raise ValueError('Known generation topology required')
        sources = [v for v in vertices if v['name']=='Source: generation-kafka-source -> Map']
        if len(sources)!=1 or not any(v['name']=='KeyedProcess -> Sink: Writer -> Sink: Committer' for v in vertices):
            raise ValueError('Known generation topology required')
        attempts=[];source_tasks=[]
        for vertex in vertices:
            identity(vertex['id'])
            parallelism=integer(vertex['parallelism'],1,binding['partitions'])
            subtasks=self._get('/vertices/'+vertex['id'])['subtasks']
            if len(subtasks)!=parallelism or sorted(t['subtask'] for t in subtasks)!=list(range(parallelism)):
                raise ValueError('Complete task attempt identity required')
            for task in subtasks:
                if task['status']!='RUNNING' or not task.get('taskmanager-id'):
                    raise ValueError('Generation task is not running')
                integer(task['attempt'],0,2**31-1);integer(task['start-time'],1,2**63-1)
                attempts.append(f"{vertex['id']}:{task['subtask']}:{task['attempt']}:{task['start-time']}:{task['taskmanager-id']}")
                if vertex is sources[0]:
                    source_tasks.append((vertex['id'],task['subtask']))
        restart=self._get('/metrics',get='numRestarts')
        if len(restart)!=1 or restart[0]['id']!='numRestarts':
            raise ValueError('Generation restart evidence unavailable')
        return tuple(sorted(attempts)),integer(int(restart[0]['value']),0,2**31-1),source_tasks

    def checkpoint(self, binding):
        require_binding(binding)
        self._io_deadline=time.monotonic()+self.timeout
        deadline=self.clock()+self.timeout
        try:
            before,restarts,tasks=self._identity(binding)
            # This REST endpoint is keyed by subtask, not execution attempt.
            # Following an in-job failover it may still expose old-attempt
            # metrics. Require an explicitly restored NEW job ID instead of
            # claiming those cached offsets prove the replacement attempt.
            if restarts != 0 or any(int(attempt.split(':',4)[2]) != 0 for attempt in before):
                raise ValueError('Restarted generation requires a fresh restored job identity')
            offsets={}
            topic=binding['input_topic'].replace('.','_')
            pattern=re.compile(r'.*\.KafkaSourceReader\.topic\.'+re.escape(topic)+r'\.partition\.([0-9]+)\.committedOffset')
            for vertex,subtask in tasks:
                suffix=f'/vertices/{vertex}/subtasks/{subtask}/metrics'
                metrics=self._get(suffix)
                names=[r['id'] for r in metrics if pattern.fullmatch(r['id'])]
                if names:
                    for metric in self._get(suffix,get=','.join(names)):
                        match=pattern.fullmatch(metric['id'])
                        if match is None or metric['id'] not in names:
                            raise ValueError('Unknown source checkpoint metric')
                        partition=integer(int(match[1]),0,binding['partitions']-1)
                        if partition in offsets:
                            raise ValueError('Ambiguous source partition ownership')
                        offsets[partition]=integer(int(metric['value']),-1,2**63-1)
            if set(offsets)!=set(range(binding['partitions'])):
                raise ValueError('Complete source-local checkpoint metrics required')
            observed=math.ceil(self.clock())
            while True:
                body=self._get('/checkpoints')
                completed=body.get('latest',{}).get('completed')
                if completed and completed['status']=='COMPLETED':
                    latest=max([completed['id'],*[entry['id'] for entry in body.get('history',[])]])
                    started=completed['trigger_timestamp']//1000
                    if started>observed and latest==completed['id']:
                        break
                if self.clock()>=deadline:
                    raise ValueError('Fresh completed checkpoint observation timed out')
                self.wait(.5)
            after,restarts_after,_=self._identity(binding)
            confirmed=int(self.clock())
            ref=f"indicator-v2/{binding['id']}/{self.job_id}/chk-{completed['id']}"
            uri=urlsplit(completed['external_path'])
            if uri.query or uri.fragment or not uri.scheme or not uri.path.endswith('/'+ref):
                raise ValueError('Generation checkpoint storage binding mismatch')
            result=Checkpoint(generation=binding['id'],job_id=self.job_id,job_name='stock-generation-indicator-v2-'+binding['id'],
                job_state='RUNNING',checkpoint_id=completed['id'],latest_checkpoint_id=latest,status='COMPLETED',checkpoint_ref=ref,
                input_topic=binding['input_topic'],output_topic=binding['output_topic'],
                next_offsets=tuple(max(0,offsets[n]) for n in range(binding['partitions'])),
                started_at=started,completed_at=completed['latest_ack_timestamp']//1000,
                offset_proof='SOURCE_CHECKPOINT_COMMIT_METRIC_BEFORE_BARRIER',offsets_observed_at=observed,offsets_confirmed_at=confirmed,
                observed_job_id=self.job_id,confirmed_job_id=self.job_id,task_attempts_before=before,task_attempts_after=after,
                restarts_before=restarts,restarts_after=restarts_after,
                uninitialized_partitions=tuple(n for n in range(binding['partitions']) if offsets[n]==-1))
            validate_checkpoint(binding,result,[-1]*binding['partitions'],now=int(self.clock()),max_age=self.timeout+15)
            return result
        except (KeyError,TypeError,AttributeError,OverflowError) as error:
            raise ValueError('Malformed native checkpoint evidence') from error
