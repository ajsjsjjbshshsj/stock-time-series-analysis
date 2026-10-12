"""DB-first core coverage, bounded sources and current-day insert ownership."""
from datetime import datetime, timezone, timedelta, time as daytime
from pathlib import Path
import time
from app.preparation.contracts import TZ, identity, timestamp, iso
from app.preparation.gap_plan import make_coverage_snapshot, plan_daily, plan_basic, eligible_bits
from app.preparation.gap_importer import import_gap_candidates
from app.preparation.gap_suspensions import cached_no_quote
from app.preparation.gap_acquisition import verify_gap_shard, validate_request
from app.preparation.importer import FileReceiptStore
from app.preparation.service import PreparationFailure
from app.preparation.worker import require_root_lock
from app.nightly.contracts import freeze_market, parse_run_at, market_day
from app.nightly.symbol_periods import reference_rows
from app.nightly.universe import verify_reference
from app.nightly.acquisition import NativeDayAcquirer
from app.repositories.preparation_repository import ERRORS


class GapUpdateService:
    def __init__(self, tasks, coverage, imports, calendar, universe, acquirer,
                 reference_loader, batch_store, data_root, *, clock=time.time):
        self.tasks, self.coverage, self.imports = tasks, coverage, imports
        self.calendar, self.universe, self.acquirer = calendar, universe, acquirer
        self.reference_loader, self.batch_store = reference_loader, batch_store
        self.data_root, self.clock = Path(data_root).resolve(), clock
        self.tasks.prepare_batch = self.prepare_batch

    def _now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def _local_reference(self):
        pinned = self.universe.pinned_reference
        if pinned is not None:
            bundle = verify_reference(pinned['source_directory'])
            if bundle != pinned:raise ValueError('PINNED_REFERENCE_CHANGED')
            return bundle
        candidates = list((self.data_root/'gap_references').glob('*/complete.json'))
        candidates += list((self.data_root/'market_updates').glob('*/attempts/*/reference/complete.json'))
        if not candidates:return None
        bundles = [verify_reference(path.parent) for path in sorted(candidates)]
        return max(bundles, key=lambda value: timestamp(value['acquired_at']))

    def prepare_batch(self, start, end, *, now, pinned_reference=None, acquire_reference=True, persist=True):
        first, last = market_day(start), market_day(end)
        local = now.astimezone(TZ)
        cutoff = local.date() if local.time() > daytime(16) else local.date()-timedelta(days=1)
        if first > last or last > cutoff:raise ValueError('GAP_RANGE_NOT_COMPLETED')
        if pinned_reference is not None:
            bundle = verify_reference(pinned_reference['source_directory'])
            if bundle != pinned_reference:raise ValueError('PINNED_REFERENCE_CHANGED')
        elif not acquire_reference:
            bundle = self._local_reference()
            if bundle is None:raise ValueError('LOCAL_VERIFIED_REFERENCE_REQUIRED')
        else:
            require_root_lock(self.data_root)
            # New nightly day gets a fresh master; repeated submissions that day
            # reuse its sealed query, while historical callers can pin a bundle.
            bundle = self.universe.source_reference(self.data_root/'gap_references'/local.date().isoformat())
        calendar = self.calendar.get(start, end, now, acquire=acquire_reference)
        if calendar is None:raise ValueError('LOCAL_VERIFIED_CALENDAR_REQUIRED')
        members = reference_rows(bundle['rows'])
        codes = sorted({row['ts_code'] for row in members if row['ts_code'].endswith(('.SH', '.SZ'))
            and row['ts_code'][:1] in '036'})
        # make_coverage_snapshot validates/classifies the full reference; scans
        # need only eligible A identities, excluding the archived T600018 alias.
        from app.nightly.contracts import is_a_share
        codes = [code for code in codes if is_a_share(code)]
        snapshot = make_coverage_snapshot(members, calendar, start, end,
            self.coverage.iter_keys('stock_daily', codes, start, end),
            self.coverage.iter_keys('stock_daily_basic', codes, start, end),
            reference_sha256=bundle['manifest_sha256'], acquired_at=bundle['acquired_at'])
        valid = eligible_bits(snapshot)
        missing_keys=((code,day) for code,bits in valid.items() for i,day in enumerate(snapshot['sessions'])
            if bits & (1 << i) and not int(snapshot['daily_bits'][code],16) & (1 << i))
        no_quote_sources=cached_no_quote(self.acquirer.source_store,missing_keys)
        no_quote_bits={code:0 for code in valid}
        positions={day:i for i,day in enumerate(snapshot['sessions'])}
        refs={}
        for (code,day),proof in no_quote_sources.items():
            no_quote_bits[code]|=1 << positions[day]
            for ref in proof['references']:refs[identity(ref)]=ref
        snapshot['no_quote_bits']={code:format(bits,'x') for code,bits in no_quote_bits.items()}
        snapshot['no_quote_refs']=[refs[key] for key in sorted(refs)]
        snapshot['coverage_sha256']=identity({key:value for key,value in snapshot.items() if key!='coverage_sha256'})
        daily, basic = plan_daily(snapshot), plan_basic(snapshot, [])
        missing_daily = sum((bits & ~int(snapshot['daily_bits'][code], 16) & ~no_quote_bits[code]).bit_count() for code, bits in valid.items())
        missing_basic = sum((int(snapshot['daily_bits'][code], 16) & ~int(snapshot['basic_bits'][code], 16)).bit_count() for code in valid)
        summary = dict(status='DRY_RUN', strategy='market-gap-v1', start=start, end=end,
            sessions=len(snapshot['sessions']), stock_count=len(snapshot['members']),
            existing_counts=snapshot['existing_counts'], planned_missing_keys=missing_daily+missing_basic,
            missing_daily=missing_daily, missing_basic=missing_basic, request_segments=len(daily)+len(basic),
            verified_no_quote=len(no_quote_sources),
            api_requests=0, verification_mode='missing-only', existing_data_revalidated=False, model_ready=False)
        if not persist:return summary
        batch = self.batch_store.create_or_load(snapshot, daily)
        self.batch_store.save(Path(batch['path'])/'reference.json',
            dict(source_directory=bundle['source_directory'], manifest_sha256=bundle['manifest_sha256']), immutable=True)
        return batch

    def submit(self, day, run_at='20:00'):
        now = self._now()
        scheduled = datetime.combine(market_day(day), parse_run_at(run_at), TZ)
        if now < scheduled:raise ValueError('MARKET_UPDATE_NOT_DUE')
        from app.preparation.worker import PreparationWorker
        with PreparationWorker(self.tasks, self).maintenance_lock():
            return self.tasks.submit(day, scheduled_for=scheduled.isoformat(), now=now.isoformat())

    def _stage(self, task, token, phase, progress=None):
        patch = dict(phase=phase, updated_at=self._now().isoformat())
        if progress is not None:patch['progress'] = progress
        if not self.tasks.update(task['task_id'], token, patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')

    def _followup(self,batch,request,shard):
        path=Path(batch['path'])/'followups'/(request['id']+'.json')
        snapshot=batch['snapshot'];index={day:i for i,day in enumerate(snapshot['sessions'])}
        keys=[(row['ts_code'],f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:]}") for row in shard['rows']]
        new_keys=[key for key in keys if not int(snapshot['daily_bits'][key[0]],16) & (1 << index[key[1]])]
        if path.exists():
            body=self.batch_store.read(path)
            if body['batch_sha256']!=batch['full_sha256'] or body['source_request_id']!=request['id']:
                raise ValueError('GAP_FOLLOWUP_BATCH_CHANGED')
            allowed=set(new_keys)
            for followup in body['requests']:
                validate_request(followup,snapshot)
                if not {tuple(key) for key in followup['expected_keys']}<=allowed:
                    raise ValueError('GAP_FOLLOWUP_OUTSIDE_ACTUAL_QUOTES')
            return body
        plan=plan_basic(snapshot,new_keys,include_existing=False)
        return self.batch_store.store_followup_plan(batch,request['id'],plan)

    def _verify_no_quote(self,state,folder,daily_missing):
        keys={tuple(key) for key in state['verified_no_quote']}
        if not keys<=daily_missing:raise ValueError('NO_QUOTE_OUTSIDE_TARGET')
        for code,day in sorted(keys):
            proof=state['no_quote_sources'].get(code)
            if proof is None:raise ValueError('NO_QUOTE_PROOF_REQUIRED')
            if proof['source_kind']=='registry':
                found=cached_no_quote(self.acquirer.source_store,[(code,day)],references=proof['references'])
                if (code,day) not in found:raise ValueError('NO_QUOTE_REGISTRY_PROOF_CHANGED')
            elif proof['source_kind']=='native':
                root=folder/'suspensions'/code
                if Path(proof['directory']).resolve()!=root.resolve():raise ValueError('NO_QUOTE_SOURCE_OUTSIDE_ROOT')
                def forbidden(*args):raise ValueError('NO_QUOTE_COMPLETION_PROOF_REQUIRED')
                shard=NativeDayAcquirer(forbidden,forbidden,clock=self.clock).collect(day,['suspend_d'],root,query_codes=[code])['shards'][0]
                if (shard['raw_sha256']!=proof['raw_sha256'] or shard['manifest_sha256']!=proof['manifest_sha256']
                        or not shard['rows'] or not all(row['suspend_type']=='S' and row['suspend_timing'] is None for row in shard['rows'])):
                    raise ValueError('NO_QUOTE_NATIVE_PROOF_CHANGED')
            else:raise ValueError('NO_QUOTE_PROOF_KIND_INVALID')
        return keys

    def execute(self, task, *, owner_token):
        require_root_lock(self.data_root)
        task = self.tasks.fence(task['task_id'], owner_token, now=self._now().isoformat())
        try:
            return self._execute(task, owner_token)
        except PreparationFailure as error:
            kind, deadline = error.kind, error.retry_not_before
        except (ValueError, KeyError, TypeError, OSError):
            kind, deadline = 'SCHEMA', None
        except RuntimeError as error:
            if str(error).startswith('OWNER'):raise
            kind, deadline = (str(error) if str(error) in ERRORS else 'STORAGE_UNAVAILABLE'), None
        waiting = kind in {'MINUTE_QUOTA', 'HOURLY_QUOTA', 'QUOTA_COOLDOWN', 'REQUEST_STATE_BUSY'}
        patch = dict(status='WAITING_RETRY' if waiting else 'FAILED', error_kind=kind,
            updated_at=self._now().isoformat(), result=dict(data_ready=False, model_ready=False,
                verification_mode='missing-only', existing_data_revalidated=False))
        if waiting:
            patch['retry_not_before'] = datetime.fromtimestamp(max(deadline or 0, self.clock()+1), timezone.utc).isoformat()
        if not self.tasks.update(task['task_id'], owner_token, patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])

    def _execute(self, task, token):
        day = task['trade_date']
        batch = self.batch_store.load(task['requirements_version'])
        snapshot = batch['snapshot']
        reference = self.batch_store.read(Path(batch['path'])/'reference.json')
        source = verify_reference(reference['source_directory'])
        if source['manifest_sha256'] != snapshot['reference_sha256'] or reference['manifest_sha256'] != source['manifest_sha256']:
            raise ValueError('GAP_REFERENCE_CHANGED')
        universe = freeze_market(day, snapshot['members'], source['manifest_sha256'], source['acquired_at'], schema_version=2)
        self.tasks.bind_universe(task['task_id'], token, universe, now=self._now().isoformat())
        guard = lambda session: self.tasks.fence(task['task_id'], token, now=self._now().isoformat(), session=session)
        self.reference_loader.ensure_from_snapshot(universe, universe['codes'], owner_guard=guard)
        folder = Path(batch['path'])/'days'/day
        state_path = folder/'progress.json'
        state = self.batch_store.read(state_path) if state_path.exists() else dict(
            completed_requests=[], inserted=0, already_present=0, api_requests=0, cache_hits=0,
            verified_no_quote=[], no_quote_sources={}, unresolved_keys=[], conflicts=[])
        target = set(universe['codes'])
        index = snapshot['sessions'].index(day)
        daily_missing = {(code, day) for code in target if not int(snapshot['daily_bits'][code], 16) & (1 << index)}
        basic_missing = {(code, day) for code in target if int(snapshot['daily_bits'][code], 16) & (1 << index)
                         and not int(snapshot['basic_bits'][code], 16) & (1 << index)}
        snapshot_no_quote={key for key in daily_missing if int(snapshot.get('no_quote_bits',{}).get(key[0],'0'),16) & (1 << index)}
        bound_sources=cached_no_quote(self.acquirer.source_store,snapshot_no_quote,references=snapshot.get('no_quote_refs',[]))
        if set(bound_sources)!=snapshot_no_quote:raise ValueError('FROZEN_NO_QUOTE_PROOF_CHANGED')
        state.setdefault('no_quote_sources',{})
        for (code,date),proof in bound_sources.items():
            state['no_quote_sources'][code]=proof
            if [code,date] not in state['verified_no_quote']:state['verified_no_quote'].append([code,date])
        self._verify_no_quote(state,folder,daily_missing)
        by_id={r['id']:r for r in batch['daily_plan']+batch['basic_plan']}
        requests=[by_id[rid] for rid in batch['request_days'][day]]
        # Quotes first so their immutable basic followups are available; do not
        # scan every unrelated parent/day plan on each claimed market date.
        requests.sort(key=lambda request:(request['method']!='daily',request['expected_codes'],request['start']))
        budget, checked = 0, set(state['completed_requests'])
        verified_receipts=[]
        schemas={}
        self.imports.owner_guard = guard
        try:
            cursor = 0
            while cursor < len(requests):
                request = requests[cursor]; cursor += 1
                already_completed = request['id'] in checked
                root = Path(batch['path'])/'sources'/request['id']
                proof = root/'complete.json'
                if not proof.exists() and budget >= 25:
                    return self._yield(task, token, state, state_path)
                self._stage(task, token, 'ACQUIRE', dict(completed_requests=len(checked), total_requests=len(requests)))
                acquired = self.acquirer.collect([request], root, snapshot=snapshot)
                budget += acquired['api_requests']
                self.tasks.fence(task['task_id'], token, now=self._now().isoformat())
                shard = acquired['shards'][0]
                rows = []
                actual = []
                for raw in shard['rows']:
                    compact = raw['trade_date']; date = f'{compact[:4]}-{compact[4:6]}-{compact[6:]}'
                    actual.append((raw['ts_code'], date))
                    if date == day:
                        row = dict(raw, trade_date=date)
                        if request['method'] == 'daily_basic':row['source'] = 'TUSHARE'
                        rows.append(row)
                if request['method'] == 'daily':
                    saved = self._followup(batch,request,shard)
                    for extra in saved['requests']:
                        if any(key[1] == day for key in extra['expected_keys']) and extra['id'] not in {r['id'] for r in requests}:
                            requests.append(extra)
                    seen = {(row['ts_code'], row['trade_date']) for row in rows}
                    absent = [(code, date) for code, date in request['expected_keys'] if date == day and (code, date) not in seen]
                    state['unresolved_keys'] += [list(key) for key in absent if list(key) not in state['unresolved_keys']]
                table = 'stock_daily' if request['method'] == 'daily' else 'stock_daily_basic'
                candidates = {table: sorted(rows, key=lambda row: (row['ts_code'], row['trade_date']))}
                if table not in schemas:schemas[table]=self.coverage.schema(table)
                result = import_gap_candidates(self.imports, candidates,
                    binding=dict(batch_sha256=batch['full_sha256'], coverage_sha256=snapshot['coverage_sha256'], request_sha256=request['id']),
                    allowed_keys={table: [key for key in request['expected_keys'] if key[1] == day]},
                    schema={table: schemas[table]}, receipt_store=FileReceiptStore(folder/'receipts'/request['id']),
                    on_progress=lambda progress: self._stage(task, token, 'IMPORT', progress))
                if not already_completed:
                    state['inserted'] += result['inserted']; state['already_present'] += result['already_present']
                state['conflicts'] += result['conflicts']
                verified_receipts.append((request,candidates,table))
                if not already_completed:
                    state['api_requests'] += acquired['api_requests']; state['cache_hits'] += acquired['cache_hits']
                checked.add(request['id']); state['completed_requests'] = sorted(checked)
                self.batch_store.save(state_path, state)
            # Resolve only missing-quote keys; trusted empty queries alone never
            # excuse them. Use exact single-code/single-day suspension evidence.
            unresolved = {tuple(key) for key in state['unresolved_keys']}
            noquote = {tuple(key) for key in state['verified_no_quote']}
            for code, date in sorted(unresolved-noquote):
                root = folder/'suspensions'/code
                native = NativeDayAcquirer(self.acquirer.client_factory, self.acquirer.requests_factory, clock=self.clock)
                if not (root/'complete.json').exists() and budget >= 25:return self._yield(task, token, state, state_path)
                # Reuse registered evidence first, which revalidates its hashes.
                store = self.acquirer.source_store
                refs = store.repository.list('suspend_d', [code], date, date)
                evidence = store.load(refs, codes=[code], start=date, end=date) if refs else None
                events = None
                if evidence is not None and (code, date) in evidence['queried_suspensions']:
                    events = evidence['suspend_d']; state['cache_hits'] += 1
                    proof=dict(source_kind='registry',references=refs)
                if events is None:
                    existed = (root/'complete.json').exists()
                    acquired = native.collect(date, ['suspend_d'], root, query_codes=[code])
                    events = acquired['shards'][0]['rows']
                    shard=acquired['shards'][0]
                    proof=dict(source_kind='native',directory=str(root),raw_sha256=shard['raw_sha256'],manifest_sha256=shard['manifest_sha256'])
                    budget += int(not existed); state['api_requests'] += int(not existed)
                if events and all(row['suspend_type'] == 'S' and row['suspend_timing'] is None for row in events):
                    noquote.add((code, date))
                    state['no_quote_sources'][code]=proof
                state['verified_no_quote'] = [list(key) for key in sorted(noquote)]
                self.tasks.fence(task['task_id'], token, now=self._now().isoformat())
                self.batch_store.save(state_path, state)
        finally:
            self.imports.owner_guard = None
        self._stage(task, token, 'VERIFY')
        if self.batch_store.load(batch['batch_id']) != batch:
            raise ValueError('GAP_BATCH_BINDING_CHANGED')
        noquote=self._verify_no_quote(state,folder,daily_missing)
        self.imports.owner_guard=guard
        try:
            for request,candidates,table in verified_receipts:
                verify_gap_shard(Path(batch['path'])/'sources'/request['id'],request,snapshot=snapshot)
                result=import_gap_candidates(self.imports,candidates,
                    binding=dict(batch_sha256=batch['full_sha256'],coverage_sha256=snapshot['coverage_sha256'],request_sha256=request['id']),
                    allowed_keys={table:[key for key in request['expected_keys'] if key[1]==day]},schema={table:schemas[table]},
                    receipt_store=FileReceiptStore(folder/'receipts'/request['id']),on_progress=lambda progress:None)
                state['conflicts']+=result['conflicts']
        finally:self.imports.owner_guard=None
        # Final existence check is still keys only, bounded to this claimed day.
        daily = set(self.coverage.iter_keys('stock_daily', sorted(target), day, day))
        basic = set(self.coverage.iter_keys('stock_daily_basic', sorted(target), day, day))
        missing = (daily_missing-daily-noquote) | (basic_missing-basic)
        new_day_keys = daily_missing & daily
        missing |= new_day_keys-basic
        result = dict(skipped_existing_keys=sum(int(snapshot[field][code],16) & (1 << index) != 0
            for field in ('daily_bits','basic_bits') for code in target),
            planned_missing_keys=len(daily_missing-snapshot_no_quote)+len(basic_missing), request_segments=len(requests),
            api_requests=state['api_requests'], cache_hits=state['cache_hits'], inserted=state['inserted'],
            already_present=state['already_present'], verified_no_quote=len(noquote), unresolved_keys=len(missing),
            conflicts=state['conflicts'], verification_mode='missing-only', existing_data_revalidated=False,
            data_ready=not missing and not state['conflicts'], model_ready=False)
        patch = dict(status='SUCCEEDED' if result['data_ready'] else 'PARTIAL', phase='VERIFY', error_kind=None,
            result=result, progress=dict(completed_requests=len(checked), total_requests=len(requests)), updated_at=self._now().isoformat())
        if not self.tasks.update(task['task_id'], token, patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])

    def _yield(self, task, token, state, state_path):
        self.tasks.fence(task['task_id'], token, now=self._now().isoformat())
        self.batch_store.save(state_path, state)
        patch = dict(status='WAITING_RETRY', error_kind=None, updated_at=self._now().isoformat(),
            retry_not_before=(self._now()+timedelta(seconds=1)).isoformat(),
            progress=dict(completed_requests=len(state['completed_requests']), reason='REQUEST_BUDGET_YIELD'))
        if not self.tasks.update(task['task_id'], token, patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])
