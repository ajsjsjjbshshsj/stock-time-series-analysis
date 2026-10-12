"""A fenced market-day pipeline. Existing SQL rows and model universes are immutable."""
from datetime import datetime,timezone
from pathlib import Path
import re,time
from sqlalchemy.exc import SQLAlchemyError,IntegrityError

from app.preparation.contracts import TZ,iso,identity
from app.preparation.stock_inventory import build_stock_inventory
from app.preparation.importer import insert_candidates,FileReceiptStore
from app.preparation.service import PreparationFailure
from app.repositories.preparation_repository import ERRORS
from app.repositories.market_repair_repository import MarketRepairRepository
from app.market_data.repair_contract import canonical
from app.market_data.forward_snapshot import read,write,digest
from .contracts import market_day,parse_run_at,validate_market
from .acquisition import register_day_evidence,supplemental_requests

def sealed(path,value=None):
    proof=path.with_suffix('.proof.json')
    if path.exists():
        if not proof.exists() or read(proof)!=dict(sha256=digest(path)):raise ValueError('MARKET_FILE_HASH_INVALID')
        current=read(path)
        if value is not None and current!=canonical(value):raise ValueError('MARKET_FILE_BINDING_CHANGED')
        return current
    if value is None:raise ValueError('MARKET_FILE_MISSING')
    write(path,canonical(value),exclusive=True);write(proof,dict(sha256=digest(path)),exclusive=True)
    return canonical(value)

class MarketUpdateService:
    def __init__(self,tasks,market,evidence,calendar,universe_provider,acquirer,reference_loader,data_root,*,clock=time.time):
        self.tasks=tasks;self.market=market;self.evidence_store=evidence;self.calendar_store=calendar
        self.universe_provider=universe_provider;self.acquirer=acquirer;self.reference_loader=reference_loader
        self.data_root=Path(data_root).resolve();self.clock=clock

    def _now(self):return datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()

    def _calendar(self,day,now,*,acquire):
        value=self.calendar_store.get(day,day,now,acquire=acquire)
        if value is None or value.get('source')!='tushare.trade_cal':raise ValueError('CALENDAR_COVERAGE_REQUIRED')
        selected=[r for r in value['rows'] if r['cal_date']==day]
        if len(selected)!=1 or type(selected[0]['is_open']) is not int or selected[0]['is_open']!=1:
            raise ValueError('MARKET_DAY_NOT_OPEN_OR_UNKNOWN')
        return value

    def submit(self,day,run_at='20:00'):
        target=market_day(day);now=datetime.fromtimestamp(self.clock(),timezone.utc)
        scheduled=datetime.combine(target,parse_run_at(run_at),TZ)
        if now<scheduled:raise ValueError('MARKET_UPDATE_NOT_DUE')
        self._calendar(day,now,acquire=True)
        return self.tasks.submit(day,scheduled_for=scheduled.isoformat(),now=now.isoformat())

    def _stage(self,task,token,phase,progress=None):
        patch=dict(phase=phase,updated_at=self._now())
        if progress is not None:patch['progress']=progress
        if not self.tasks.update(task['task_id'],token,patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')

    def _inventory(self,snapshot,baseline,calendar,*,references=None):
        day=snapshot['trade_date'];refs=[] if references is None else references
        if references is None:
            for method in ('daily','daily_basic','adj_factor','suspend_d'):
                refs.extend(self.evidence_store.repository.list(method,snapshot['codes'],day,day))
        evidence=self.evidence_store.load(refs,codes=snapshot['codes'],start=day,end=day);evidence['calendar']=calendar
        universe=dict(pool_version='market_'+snapshot['member_sha256'][:32],codes=snapshot['codes'],stock_count=snapshot['stock_count'])
        window=dict(start=day,end=day,requested_start=day,requested_end=day,empty=False,cutoff_reason=None)
        inventory=build_stock_inventory(universe,window,baseline,evidence,
            allow_verified_legacy_float32=snapshot['schema_version']==2)
        if snapshot['schema_version']==2:
            inventory['legacy_precision_source_manifest_sha256']=sorted({ref['manifest_sha256'] for ref in refs})
            inventory['verified_evidence_refs']=refs
        return inventory

    def _reference_source(self,folder,attempt,snapshot=None):
        """Persist matching provenance before binding; never reseal a mismatch."""
        from .universe import verify_reference
        path=folder/'reference_source.json'
        if path.exists():
            reference=sealed(path)
            if set(reference)!={'source_directory','manifest_sha256'}:raise ValueError('REFERENCE_BINDING_CHANGED')
            bundle=verify_reference(reference['source_directory'])
            if bundle['manifest_sha256']!=reference['manifest_sha256']:raise ValueError('REFERENCE_SOURCE_IDENTITY_INVALID')
        elif snapshot is not None:
            # Recover only an original, completed source matching the bound
            # immutable snapshot. Do not call the SDK under a new attempt.
            candidates=[p.parent for p in sorted((folder/'attempts').glob('*/reference/complete.json'))]
            pinned=getattr(self.universe_provider,'pinned_reference',None)
            if pinned is not None:candidates.append(Path(pinned['source_directory']))
            bundle=None
            for directory in candidates:
                candidate=verify_reference(directory)
                if candidate['manifest_sha256']==snapshot['source_manifest_sha256']:
                    bundle=candidate;break
            if bundle is None:raise ValueError('MATCHING_REFERENCE_SOURCE_REQUIRED')
        else:bundle=self.universe_provider.source_reference(attempt/'reference')
        if snapshot is not None and (bundle['manifest_sha256']!=snapshot['source_manifest_sha256'] or bundle['acquired_at']!=snapshot['acquired_at']):
            raise ValueError('REFERENCE_SOURCE_IDENTITY_INVALID')
        sealed(path,dict(source_directory=bundle['source_directory'],manifest_sha256=bundle['manifest_sha256']))
        return bundle

    def execute(self,task,*,owner_token):
        task=self.tasks.fence(task['task_id'],owner_token,now=self._now())
        try:return self._execute(task,owner_token)
        except PreparationFailure as error:failure=error
        except IntegrityError:failure=PreparationFailure('TRANSACTION_FAILED')
        except SQLAlchemyError:failure=PreparationFailure('STORAGE_UNAVAILABLE')
        except (ValueError,KeyError,TypeError,OSError):failure=PreparationFailure('SCHEMA')
        except RuntimeError as error:
            if str(error).startswith('OWNER'):raise
            failure=PreparationFailure(str(error) if str(error) in ERRORS else 'STORAGE_UNAVAILABLE')
        status='WAITING_RETRY' if failure.kind in {'MINUTE_QUOTA','HOURLY_QUOTA','QUOTA_COOLDOWN','REQUEST_STATE_BUSY'} else 'FAILED'
        patch=dict(status=status,error_kind=failure.kind,updated_at=self._now(),result=dict(data_ready=False,model_ready=False,error_kind=failure.kind))
        if status=='WAITING_RETRY':
            now=self.clock();deadline=failure.retry_not_before
            if deadline is None or deadline<=now:deadline=now+60
            patch['retry_not_before']=datetime.fromtimestamp(deadline,timezone.utc).isoformat()
        if not self.tasks.update(task['task_id'],owner_token,patch):raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])

    def _execute(self,task,token):
        day=task['trade_date'];self._stage(task,token,'RESOLVE')
        calendar=self._calendar(day,datetime.fromisoformat(task['created_at']),acquire=True)
        folder=(self.data_root/'market_updates'/task['task_id']).resolve()
        if not folder.is_relative_to(self.data_root):raise ValueError('MARKET_TASK_OUTSIDE_ROOT')
        folder.mkdir(parents=True,exist_ok=True)
        sealed(folder/'binding.json',dict(request=task['request'],identity=task['identity']))
        attempt=folder/'attempts'/f"{task['attempt_no']:06d}";attempt.mkdir(parents=True,exist_ok=True)
        self._stage(task,token,'REFERENCE')
        snapshot=task['universe']
        reference_directory=None
        if snapshot is None:
            self.universe_provider.on_progress=lambda done,total:self._stage(task,token,'REFERENCE',dict(completed_requests=done,total_requests=total))
            if task['requirements_version']=='market-v2':
                from .contracts import freeze_market
                from .symbol_periods import reference_rows
                bundle=self._reference_source(folder,attempt)
                reference_directory=bundle['source_directory']
                snapshot=freeze_market(day,reference_rows(bundle['rows']),bundle['manifest_sha256'],bundle['acquired_at'],schema_version=2)
            else:snapshot=self.universe_provider.freeze(day,attempt/'reference')
            validate_market(snapshot)
            if datetime.fromisoformat(snapshot['acquired_at']).timestamp()>self.clock():
                raise ValueError('REFERENCE_ACQUIRED_IN_FUTURE')
            task=self.tasks.bind_universe(task['task_id'],token,snapshot,now=self._now())
        validate_market(snapshot);sealed(folder/'universe.json',snapshot)
        if snapshot['schema_version']==2:
            bundle=self._reference_source(folder,attempt,snapshot)
            reference_directory=bundle['source_directory']
        self._stage(task,token,'INVENTORY')
        current=canonical(self.market.capture(snapshot['codes'],day,day))
        listed={r['ts_code']:str(r['list_date']) for r in current['stock_basic'] if r.get('list_date')}
        source_dates={r['ts_code']:r['list_date'] for r in snapshot['members']}
        if any(source_dates[c]!=value for c,value in listed.items()):raise PreparationFailure('INVALID_EXISTING_DATA')
        missing=sorted(set(snapshot['codes'])-set(listed))
        if missing:
            if self.reference_loader is None:raise PreparationFailure('INVALID_EXISTING_DATA')
            self.reference_loader.ensure_from_snapshot(snapshot,missing,owner_guard=lambda session:self.tasks.fence(task['task_id'],token,now=self._now(),session=session))
            current=canonical(self.market.capture(snapshot['codes'],day,day))
        initial_path=folder/'initial_baseline.json'
        initial=sealed(initial_path,current if not initial_path.exists() else None)
        try:MarketRepairRepository._assert_original(initial,current)
        except ValueError:raise PreparationFailure('ORIGINAL_CHANGED') from None
        base_path=attempt/'baseline.json';baseline=sealed(base_path,current if not base_path.exists() else None)
        try:MarketRepairRepository._assert_original(baseline,current)
        except ValueError:raise PreparationFailure('ORIGINAL_CHANGED') from None
        inventory=self._inventory(snapshot,baseline,calendar)
        plan=attempt/'native_methods.json'
        if plan.exists():methods=sealed(plan)
        else:
            methods=[]
            if inventory['missing_daily']:methods.extend(['daily','daily_basic','suspend_d'])
            elif inventory['missing_basic']:methods.append('daily_basic')
            if inventory['missing_factors']:methods.append('adj_factor')
            methods=sealed(plan,methods)
        guard=lambda session:self.tasks.fence(task['task_id'],token,now=self._now(),session=session)
        self._stage(task,token,'ACQUIRE')
        self.acquirer.on_progress=lambda done,total:self._stage(task,token,'ACQUIRE',dict(completed_requests=done,total_requests=total))
        acquired=self.acquirer.collect(day,methods,attempt/'native')
        register_day_evidence(self.evidence_store,snapshot,acquired['shards'],owner_guard=guard,
            reference_directory=reference_directory,
            exclusion_sink=(lambda rows:sealed(attempt/'source_exclusions.json',rows)) if snapshot['schema_version']==2 else None)
        inventory=self._inventory(snapshot,baseline,calendar)
        supplement_path=attempt/'supplement_plan.json'
        supplement=sealed(supplement_path) if supplement_path.exists() else sealed(supplement_path,supplemental_requests(inventory))
        for index,r in enumerate(supplement['requests']):
            if r['day']!=day or not set(r['query_codes'])<=set(snapshot['codes']):raise ValueError('SUPPLEMENT_OUTSIDE_UNIVERSE')
            self._stage(task,token,'ACQUIRE',dict(completed_supplement=index,total_supplement=len(supplement['requests'])))
            extra=self.acquirer.collect(day,[r['method']],attempt/'supplement'/f'{index:04d}',query_codes=r['query_codes'])
            register_day_evidence(self.evidence_store,snapshot,extra['shards'],owner_guard=guard,
                reference_directory=reference_directory,
                exclusion_sink=(lambda rows:sealed(attempt/'supplement'/f'{index:04d}'/'source_exclusions.json',rows)) if snapshot['schema_version']==2 else None)
        self._stage(task,token,'ASSEMBLE')
        inventory=self._inventory(snapshot,baseline,calendar)
        if snapshot['schema_version']==2:
            sealed(attempt/'precision_compatibility.json',dict(schema_version=1,policy='EXACT_BINARY32_THEN_DECIMAL',
                baseline_sha256=digest(folder/'initial_baseline.json'),
                source_manifest_sha256=inventory['legacy_precision_source_manifest_sha256'],
                rows=inventory['attribution']['legacy_float32_compatible']))
        self._stage(task,token,'IMPORT');self.market.owner_guard=guard
        try:
            result=insert_candidates(self.market,baseline,inventory['attribution']['candidates'],FileReceiptStore(attempt),lambda progress:self._stage(task,token,'IMPORT',progress))
        finally:self.market.owner_guard=None
        self._stage(task,token,'VERIFY')
        if not result['db_import_complete']:raise PreparationFailure('SOURCE_CONFLICT')
        current=canonical(self.market.capture(snapshot['codes'],day,day))
        try:MarketRepairRepository._assert_original(initial,current)
        except ValueError:raise PreparationFailure('ORIGINAL_CHANGED') from None
        # Same immutable sources as the sealed comparison audit. A concurrent
        # registry addition belongs to the next inventory, not this decision.
        after=self._inventory(snapshot,current,calendar,
            references=inventory['verified_evidence_refs']) if snapshot['schema_version']==2 else self._inventory(snapshot,current,calendar)
        inserted=0
        for previous in (folder/'attempts').iterdir():
            if re.fullmatch('[0-9]{6}',previous.name) and (previous/'import_receipts.json').exists():
                receipts=read(previous/'import_receipts.json')
                inserted+=sum(receipt['inserted'] for receipt in receipts['batches'].values())
        summary=dict(stock_count=snapshot['stock_count'],trade_date=day,issues=after['issues'],coverage=after['coverage'],
            data_ready=after['data_ready'],source_complete=acquired['complete'],db_import_complete=True,original_rows_unchanged=True,
            inserted_this_attempt=result['inserted'],inserted=inserted,already_equal=result['already_equal'],attempt_no=task['attempt_no'],
            supplement_budget_exhausted=supplement['exhausted'],model_ready=False)
        if snapshot['schema_version']==2:summary['legacy_float32_compatible_count']=len(after['attribution']['legacy_float32_compatible'])
        status='SUCCEEDED' if summary['data_ready'] else 'PARTIAL'
        if not self.tasks.update(task['task_id'],token,dict(status=status,result=summary,error_kind=None,updated_at=self._now())):
            raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])
