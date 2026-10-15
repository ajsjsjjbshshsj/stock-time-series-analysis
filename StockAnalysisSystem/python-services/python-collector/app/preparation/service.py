"""Preparation orchestration with fixed submission cutoff and fenced writes."""
from datetime import datetime,time as walltime,timedelta,timezone
from pathlib import Path
from app.market_data.retry_policy import RETRYABLE_TASK_ERRORS
import time
from sqlalchemy.exc import SQLAlchemyError,IntegrityError

from app.market_data.forward_snapshot import read,write,digest
from app.market_data.repair_contract import canonical
from app.market_data.repair_acquisition import validate_request as validate_market_request
from app.repositories.preparation_repository import validate_request,ERRORS
from .contracts import TZ,identity,iso
from .calendar import resolve_window
from .inventory import build_inventory
from .planner import plan_missing
from .importer import FileReceiptStore,insert_candidates


class PreparationFailure(RuntimeError):
    def __init__(self,kind,*,retry_not_before=None,http_status=None):
        self.kind=kind if kind in ERRORS else 'PROVIDER'
        self.retry_not_before=retry_not_before
        self.http_status=http_status if type(http_status) is int else None
        super().__init__(self.kind)


class DataPreparationService:
    def __init__(self,pools,tasks,market,evidence_store,calendar_store,acquirer,data_root,*,clock=time.time,reference_loader=None):
        self.pools=pools; self.tasks=tasks; self.market=market
        self.evidence_store=evidence_store; self.calendar_store=calendar_store
        self.acquirer=acquirer; self.data_root=Path(data_root).resolve()
        self.clock=clock; self.reference_loader=reference_loader

    def _now(self): return datetime.fromtimestamp(self.clock(),timezone.utc).isoformat()

    def submit(self,request):
        validate_request(request)
        self.pools.resolve(pool_version=request['pool_version'])
        submitted=datetime.fromtimestamp(self.clock(),timezone.utc)
        local=submitted.astimezone(TZ)
        cutoff=local.date() if local.time()>walltime(16) else local.date()-timedelta(days=1)
        concrete=dict(request,cutoff_date=min(iso(request['requested_end']),cutoff).isoformat())
        return self.tasks.submit(concrete,now=submitted.isoformat())

    def _inventory(self,pool,request,*,acquire,clicked=None,tables=None):
        now=datetime.fromisoformat(clicked) if clicked else datetime.fromtimestamp(self.clock(),timezone.utc)
        if 'cutoff_date' in request:
            # The server-bound cutoff survives queue delays and a 16:00 crossing.
            now=datetime.combine(iso(request['cutoff_date']),walltime(17),TZ)
        calendar=self.calendar_store.get(request['requested_start'],request['requested_end'],now,acquire=acquire)
        if calendar is None:
            return dict(data_ready=False,issues={'CALENDAR_COVERAGE_REQUIRED':1},missing_calendar=True)
        window=resolve_window(request['requested_start'],request['requested_end'],calendar,now)
        if window['empty']:
            return dict(data_ready=False,window=window,issues={'EMPTY_EFFECTIVE_RANGE':1},missing_calendar=False)
        if tables is None: tables=self.market.capture(pool['codes'],window['start'],window['end'])
        refs=[]
        for method in ('daily','daily_basic','adj_factor','suspend_d'):
            refs.extend(self.evidence_store.repository.list(method,pool['codes'],window['start'],window['end']))
        evidence=self.evidence_store.load(refs,codes=pool['codes'],start=window['start'],end=window['end'])
        evidence['calendar']=calendar
        return build_inventory(pool,window,tables,evidence)

    def inspect(self,request):
        validate_request(request)
        pool=self.pools.resolve(pool_version=request['pool_version'])
        result=self._inventory(pool,request,acquire=False)
        return self._summary(result)

    @staticmethod
    def _summary(inventory):
        return {k:inventory[k] for k in ('window','stock_count','coverage','issues','data_ready','missing_calendar','missing_reference') if k in inventory}

    def _stage(self,task,token,phase,progress=None):
        patch=dict(phase=phase,updated_at=self._now())
        if progress is not None: patch['progress']=progress
        if not self.tasks.update(task['task_id'],token,patch): raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')

    def _register(self,acquired,pool):
        for shard in acquired['shards']:
            r=shard['request']
            # daily_basic is a complete full-market date response; retain its raw
            # rows and make all frozen members available for supplementary quotes.
            expected=sorted(set(pool['codes'])|{row['ts_code'] for row in shard['rows']}) if r['method']=='daily_basic' else r['expected_codes']
            self.evidence_store.register_verified(dict(kind=r['method'],provider='tushare.'+r['method'],
                start_date=r['start'],end_date=r['end'],expected_codes=expected,rows=shard['rows']))

    @staticmethod
    def _plan(path,producer,pool,window):
        proof=path.with_suffix('.proof.json')
        if path.exists():
            if not proof.exists() or read(proof)!=dict(sha256=digest(path)): raise ValueError('REQUEST_PLAN_HASH_INVALID')
            requests=read(path)
        else:
            requests=producer()
            write(path,requests,exclusive=True); write(proof,dict(sha256=digest(path)),exclusive=True)
        if not isinstance(requests,list): raise ValueError('REQUEST_PLAN_INVALID')
        for request in requests:
            validate_market_request(request,allowed_markets=('SH','SZ','BJ'))
            if (not set(request['expected_codes'])<=set(pool['codes'])
                    or not window['start']<=request['start']<=request['end']<=window['end']):
                raise ValueError('REQUEST_PLAN_OUTSIDE_SCOPE')
        return requests

    def execute(self,task,*,owner_token):
        task=self.tasks.fence(task['task_id'],owner_token,now=self._now())
        try:
            return self._execute(task,owner_token)
        except PreparationFailure as error:
            failure=error
        except IntegrityError:
            failure=PreparationFailure('TRANSACTION_FAILED')
        except SQLAlchemyError:
            failure=PreparationFailure('STORAGE_UNAVAILABLE')
        except (ValueError,KeyError,TypeError,OSError):
            failure=PreparationFailure('SCHEMA')
        except RuntimeError as error:
            if str(error).startswith('OWNER'): raise
            failure=PreparationFailure(str(error) if str(error) in ERRORS else 'STORAGE_UNAVAILABLE')
        target='WAITING_RETRY' if failure.kind in RETRYABLE_TASK_ERRORS else 'FAILED'
        patch=dict(status=target,error_kind=failure.kind,updated_at=self._now(),result=dict(data_ready=False,error_kind=failure.kind))
        if target=='WAITING_RETRY':
            current=self.clock()
            deadline=failure.retry_not_before
            if deadline is None or deadline<=current: deadline=current+60
            patch['retry_not_before']=datetime.fromtimestamp(deadline,timezone.utc).isoformat()
        if not self.tasks.update(task['task_id'],owner_token,patch): raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
        return self.tasks.get(task['task_id'])

    def _execute(self,task,token):
        self._stage(task,token,'RESOLVE')
        pool=self.pools.resolve(pool_version=task['request']['pool_version'])
        folder=(self.data_root/'tasks'/task['task_id']).resolve()
        if not folder.is_relative_to(self.data_root): raise ValueError('TASK_PATH_OUTSIDE_ROOT')
        folder.mkdir(parents=True,exist_ok=True)
        context=dict(request=task['request'],member_sha256=pool['member_sha256'])
        binding=folder/'binding.json'
        if binding.exists():
            if read(binding)!=context: raise ValueError('TASK_BINDING_CHANGED')
        else: write(binding,context,exclusive=True)
        self._stage(task,token,'INVENTORY')
        baseline_path=folder/'baseline.json'; baseline_proof=folder/'baseline.proof.json'
        if baseline_path.exists():
            if not baseline_proof.exists() or read(baseline_proof)!=dict(sha256=digest(baseline_path)):
                raise ValueError('BASELINE_HASH_INVALID')
            baseline=read(baseline_path)
            inventory=self._inventory(pool,task['request'],acquire=True,clicked=task['created_at'],tables=baseline)
        else:
            inventory=self._inventory(pool,task['request'],acquire=True,clicked=task['created_at'])
            if inventory.get('missing_reference') and self.reference_loader is not None:
                self.reference_loader.owner_guard=lambda session:self.tasks.fence(task['task_id'],token,now=self._now(),session=session)
                self.reference_loader.ensure(pool,inventory['missing_reference'],folder)
                inventory=self._inventory(pool,task['request'],acquire=True,clicked=task['created_at'])
            if inventory.get('missing_reference'): raise PreparationFailure('INVALID_EXISTING_DATA')
            if 'baseline' not in inventory:
                self._finish(task,token,'PARTIAL',self._summary(inventory))
                return self.tasks.get(task['task_id'])
            baseline=canonical(inventory['baseline'])
            write(baseline_path,baseline,exclusive=True)
            write(baseline_proof,dict(sha256=digest(baseline_path)),exclusive=True)
        plan_path=folder/'requests.json'
        planned=self._plan(plan_path,lambda:plan_missing(inventory),pool,inventory['window'])
        self._stage(task,token,'ACQUIRE',dict(completed_requests=0,total_requests=len(planned)))
        self.acquirer.on_progress=lambda done,total:self._stage(task,token,'ACQUIRE',dict(completed_requests=done,total_requests=total))
        acquired=self.acquirer.collect(planned,folder/'acquisition/initial')
        self._register(acquired,pool)
        inventory=self._inventory(pool,task['request'],acquire=False,clicked=task['created_at'],tables=baseline)
        basic_plan_path=folder/'basic_requests.json'
        def supplementary():
            queried={r['start'] for r in planned if r['method']=='daily_basic'}
            return [r for r in plan_missing(inventory) if r['method']=='daily_basic' and r['start'] not in queried]
        basic_plan=self._plan(basic_plan_path,supplementary,pool,inventory['window'])
        self._stage(task,token,'ACQUIRE',dict(completed_requests=len(planned),total_requests=len(planned)+len(basic_plan)))
        self.acquirer.on_progress=lambda done,total:self._stage(task,token,'ACQUIRE',dict(completed_requests=len(planned)+done,total_requests=len(planned)+total))
        extra=self.acquirer.collect(basic_plan,folder/'acquisition/basic')
        self._register(extra,pool)
        self._stage(task,token,'ASSEMBLE')
        inventory=self._inventory(pool,task['request'],acquire=False,clicked=task['created_at'],tables=baseline)
        candidates=inventory['attribution']['candidates']
        # Guard executes inside each write transaction, taking the task row lock.
        self.market.owner_guard=lambda session:self.tasks.fence(task['task_id'],token,now=self._now(),session=session)
        self._stage(task,token,'IMPORT')
        result=insert_candidates(self.market,baseline,candidates,FileReceiptStore(folder),
            lambda progress:self._stage(task,token,'IMPORT',progress))
        self._stage(task,token,'VERIFY')
        if not result['db_import_complete']: raise PreparationFailure('ORIGINAL_CHANGED' if result.get('original_rows_unchanged') is False else 'SOURCE_CONFLICT')
        summary=dict(self._summary(inventory),acquisition_complete=True,db_import_complete=True,
            original_rows_unchanged=result['original_rows_unchanged'],inserted=result['inserted'],already_equal=result['already_equal'],model_ready=False)
        self._finish(task,token,'SUCCEEDED' if inventory['data_ready'] else 'PARTIAL',summary)
        return self.tasks.get(task['task_id'])

    def _finish(self,task,token,status,result):
        if not self.tasks.update(task['task_id'],token,dict(status=status,result=result,error_kind=None,updated_at=self._now())):
            raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
