"""One immutable market-day run, fenced lease and explicit manual retry."""
import json,re,uuid
from datetime import datetime
from functools import wraps
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.nightly.contracts import SCOPE,VERSION,market_day,market_identity,parse_run_at,validate_market
from app.preparation.contracts import identity,timestamp
from ._preparation_sql import transaction,encode,stamp,utc,current_lock
from .preparation_repository import ERRORS,PATCH_FIELDS

PHASES={'QUEUED','RESOLVE','REFERENCE','INVENTORY','ACQUIRE','ASSEMBLE','IMPORT','VERIFY'}
TERMINAL={'SUCCEEDED','FAILED','PARTIAL'}
STATUSES=TERMINAL|{'QUEUED','RUNNING','WAITING_RETRY'}

def _safe_database_boundary(method):
    """A regular-function boundary removes generator-contextmanager chaining."""
    @wraps(method)
    def call(*args,**kwargs):
        failure=None
        try:return method(*args,**kwargs)
        except RuntimeError as error:
            if str(error) not in ('MIGRATION_REQUIRED','STORAGE_UNAVAILABLE'):raise
            failure=str(error)
        # Outside the handler AND outside a generator's throw/__exit__ context.
        raise RuntimeError(failure) from None
    return call

def _scheduled(day,value):
    target=market_day(day);scheduled=timestamp(value)
    parse_run_at(scheduled.strftime('%H:%M'))
    if scheduled.date()!=target or scheduled.second or scheduled.microsecond:raise ValueError('MARKET_SCHEDULE_INVALID')
    return scheduled

def _task(row):
    value=dict(row)
    for column,target in (('request_json','request'),('owner_json','owner'),('progress_json','progress'),('result_json','result'),('universe_json','universe')):
        raw=value.pop(column)
        value[target]=json.loads(raw) if raw is not None else None
    for field in ('not_before','created_at','updated_at','lease_until','retry_not_before'):value[field]=utc(value[field])
    request=value['request']
    if (not isinstance(request,dict) or set(request)!={'scope','trade_date','requirements_version','scheduled_for'}
            or request['scope']!=SCOPE or request['requirements_version']!=VERSION
            or value['market_scope']!=SCOPE or value['requirements_version']!=VERSION
            or str(value['trade_date'])!=request['trade_date'] or value['identity']!=market_identity(request['trade_date'])
            or value['request_sha256']!=identity(request) or value['status'] not in STATUSES
            or value['phase'] not in PHASES or type(value['attempt_no']) is not int or value['attempt_no']<1):
        raise ValueError('MARKET_TASK_IDENTITY_CHANGED')
    value['trade_date']=str(value['trade_date'])
    if stamp(_scheduled(request['trade_date'],request['scheduled_for']))!=stamp(value['not_before']):
        raise ValueError('MARKET_SCHEDULE_CHANGED')
    if value['universe'] is not None:
        validate_market(value['universe'])
        if value['universe']['trade_date']!=value['trade_date'] or identity(value['universe'])!=value['universe_sha256']:
            raise ValueError('MARKET_UNIVERSE_CHANGED')
    elif value['universe_sha256'] is not None:raise ValueError('MARKET_UNIVERSE_CHANGED')
    return value

class MarketUpdateRepository:
    def __init__(self,session_factory):self.session_factory=session_factory

    @staticmethod
    def _get(session,task_id,*,lock=False):
        if type(task_id) is not str or re.fullmatch('[0-9a-f]{32}',task_id) is None:raise ValueError('TASK_ID_INVALID')
        row=session.execute(text('SELECT * FROM market_update_run WHERE task_id=:id'+(current_lock(session) if lock else '')),dict(id=task_id)).mappings().first()
        if row is None:raise ValueError('TASK_NOT_FOUND')
        return _task(row)

    @_safe_database_boundary
    def get(self,task_id):
        with transaction(self.session_factory) as session:return self._get(session,task_id)

    @_safe_database_boundary
    def submit(self,day,*,scheduled_for,now):
        scheduled=_scheduled(day,scheduled_for);clock=stamp(now)
        if clock<stamp(scheduled):raise ValueError('MARKET_UPDATE_NOT_DUE')
        request=dict(scope=SCOPE,trade_date=day,requirements_version=VERSION,scheduled_for=scheduled.isoformat())
        key=market_identity(day)
        with transaction(self.session_factory) as session:
            row=session.execute(text('SELECT task_id FROM market_update_run WHERE identity=:i'+current_lock(session)),dict(i=key)).first()
            if row:return self._get(session,row[0])
            task_id=uuid.uuid4().hex
            try:
                with session.begin_nested():
                    session.execute(text('INSERT INTO market_update_run '
                        '(task_id,identity,trade_date,market_scope,requirements_version,request_json,request_sha256,attempt_no,not_before,status,phase,progress_json,created_at,updated_at) '
                        "VALUES (:id,:i,:day,:scope,:version,:request,:sha,1,:before,'QUEUED','QUEUED',:progress,:now,:now)"),
                        dict(id=task_id,i=key,day=day,scope=SCOPE,version=VERSION,request=encode(request),sha=identity(request),before=stamp(scheduled),progress=encode({}),now=clock))
            except IntegrityError as error:
                duplicate=bool(error.orig.args and error.orig.args[0]==1062) or session.get_bind().dialect.name=='sqlite' and getattr(error.orig,'sqlite_errorcode',None) in (1555,2067)
                if not duplicate:raise
                row=session.execute(text('SELECT task_id FROM market_update_run WHERE identity=:i'+current_lock(session)),dict(i=key)).first()
                if row is None:raise
                return self._get(session,row[0])
            return self._get(session,task_id)

    @_safe_database_boundary
    def list_runs(self,start,end):
        if market_day(start)>market_day(end):raise ValueError('MARKET_RANGE_INVALID')
        with transaction(self.session_factory) as session:
            rows=session.execute(text('SELECT task_id,trade_date,status,error_kind,created_at,not_before,retry_not_before '
                'FROM market_update_run WHERE market_scope=:scope AND requirements_version=:version AND trade_date BETWEEN :start AND :end ORDER BY trade_date'),dict(scope=SCOPE,version=VERSION,start=start,end=end)).mappings().all()
            result=[]
            for row in rows:
                item=dict(row,trade_date=str(row['trade_date']))
                for field in ('created_at','not_before','retry_not_before'):item[field]=utc(item[field])
                result.append(item)
            return result

    @_safe_database_boundary
    def earliest_date(self):
        with transaction(self.session_factory) as session:
            value=session.execute(text('SELECT MIN(trade_date) FROM market_update_run WHERE market_scope=:scope AND requirements_version=:version'),dict(scope=SCOPE,version=VERSION)).scalar()
            return str(value) if value is not None else None

    @_safe_database_boundary
    def claim(self,owner,*,now,lease_until):
        clock,until=stamp(now),stamp(lease_until);token=owner.get('token')
        if (until<=clock or type(token) is not str or re.fullmatch('[0-9a-f]{32}',token) is None
                or type(owner.get('pid')) is not int or owner['pid']<=0):raise ValueError('OWNER_OR_LEASE_INVALID')
        stamp(owner['started_at'])
        with transaction(self.session_factory) as session:
            row=session.execute(text("SELECT task_id FROM market_update_run WHERE market_scope=:scope AND requirements_version=:version AND status IN ('QUEUED','WAITING_RETRY') "
                'AND not_before<=:now AND (retry_not_before IS NULL OR retry_not_before<=:now) '
                'ORDER BY trade_date,created_at,task_id LIMIT 1'+current_lock(session)),dict(scope=SCOPE,version=VERSION,now=clock)).first()
            if row is None:return None
            result=session.execute(text("UPDATE market_update_run SET status='RUNNING',owner_token=:token,owner_json=:owner,lease_until=:until,updated_at=:now "
                "WHERE task_id=:id AND status IN ('QUEUED','WAITING_RETRY') AND not_before<=:now AND (retry_not_before IS NULL OR retry_not_before<=:now)"),
                dict(id=row[0],token=token,owner=encode(owner),until=until,now=clock))
            return self._get(session,row[0]) if result.rowcount==1 else None

    @_safe_database_boundary
    def fence(self,task_id,owner_token,*,now,session=None):
        def check(current):
            task=self._get(current,task_id,lock=True)
            if task['status']!='RUNNING' or task['owner_token']!=owner_token or task['lease_until'] is None or stamp(task['lease_until'])<=stamp(now):
                raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
            return task
        if session is not None:return check(session)
        with transaction(self.session_factory) as current:return check(current)

    @_safe_database_boundary
    def bind_universe(self,task_id,owner_token,snapshot,*,now):
        validate_market(snapshot)
        with transaction(self.session_factory) as session:
            task=self.fence(task_id,owner_token,now=now,session=session)
            if snapshot['trade_date']!=task['trade_date']:raise ValueError('MARKET_UNIVERSE_DATE_CHANGED')
            if task['universe'] is not None:
                if task['universe']!=snapshot:raise ValueError('MARKET_UNIVERSE_CHANGED')
                return task
            session.execute(text('UPDATE market_update_run SET universe_json=:value,universe_sha256=:sha,updated_at=:now WHERE task_id=:id'),
                dict(value=encode(snapshot),sha=identity(snapshot),now=stamp(now),id=task_id))
            return self._get(session,task_id)

    @_safe_database_boundary
    def heartbeat(self,task_id,owner_token,*,now,lease_until):
        clock,until=stamp(now),stamp(lease_until)
        if until<=clock:raise ValueError('LEASE_INVALID')
        with transaction(self.session_factory) as session:
            result=session.execute(text('UPDATE market_update_run SET lease_until=:until,updated_at=:now '
                "WHERE task_id=:id AND owner_token=:token AND status='RUNNING' AND lease_until>:now"),dict(id=task_id,token=owner_token,now=clock,until=until))
            return result.rowcount==1

    @_safe_database_boundary
    def update(self,task_id,owner_token,patch):
        if not patch or not set(patch)<=PATCH_FIELDS or 'updated_at' not in patch:raise ValueError('TASK_PATCH_INVALID')
        if ('status' in patch and patch['status'] not in {'RUNNING','WAITING_RETRY'}|TERMINAL
                or 'phase' in patch and patch['phase'] not in PHASES
                or patch.get('error_kind') is not None and patch['error_kind'] not in ERRORS):raise ValueError('TASK_STATE_INVALID')
        clock=stamp(patch['updated_at']);target=patch.get('status')
        if target=='WAITING_RETRY' and (patch.get('retry_not_before') is None or stamp(patch['retry_not_before'])<=clock):
            raise ValueError('FUTURE_RETRY_DEADLINE_REQUIRED')
        values={}
        for key,value in patch.items():
            if key in ('progress','result'):
                if value is not None and not isinstance(value,dict):raise ValueError('TASK_PAYLOAD_INVALID')
                values[key+'_json']=encode(value) if value is not None else None
            elif key in ('updated_at','retry_not_before'):values[key]=stamp(value) if value is not None else None
            else:values[key]=value
        if target in TERMINAL|{'WAITING_RETRY'}:values.update(owner_token=None,owner_json=None,lease_until=None)
        sql=','.join(key+'=:'+key for key in values)
        with transaction(self.session_factory) as session:
            result=session.execute(text('UPDATE market_update_run SET '+sql+" WHERE task_id=:id AND status='RUNNING' AND owner_token=:token AND lease_until>:clock"),
                dict(values,id=task_id,token=owner_token,clock=clock))
            return result.rowcount==1

    @_safe_database_boundary
    def expired(self,*,now):
        with transaction(self.session_factory) as session:
            return [_task(row) for row in session.execute(text("SELECT * FROM market_update_run WHERE market_scope=:scope AND requirements_version=:version AND status='RUNNING' AND lease_until<=:now"),dict(scope=SCOPE,version=VERSION,now=stamp(now))).mappings().all()]

    @_safe_database_boundary
    def recover(self,task_id,old_owner_token,*,now):
        with transaction(self.session_factory) as session:
            result=session.execute(text("UPDATE market_update_run SET status='QUEUED',owner_token=NULL,owner_json=NULL,lease_until=NULL,updated_at=:now "
                "WHERE task_id=:id AND owner_token=:token AND status='RUNNING' AND lease_until<=:now"),dict(id=task_id,token=old_owner_token,now=stamp(now)))
            return result.rowcount==1

    @_safe_database_boundary
    def retry(self,task_id,*,now):
        with transaction(self.session_factory) as session:
            task=self._get(session,task_id,lock=True)
            if task['status'] not in {'FAILED','PARTIAL'}:raise ValueError('ONLY_FAILED_OR_PARTIAL_CAN_RETRY')
            session.execute(text("UPDATE market_update_run SET status='QUEUED',phase='QUEUED',attempt_no=attempt_no+1,retry_not_before=NULL,error_kind=NULL,updated_at=:now WHERE task_id=:id"),
                dict(now=stamp(now),id=task_id))
            return self._get(session,task_id)

    @_safe_database_boundary
    def next_retry(self):
        with transaction(self.session_factory) as session:
            value=session.execute(text("SELECT MIN(CASE WHEN retry_not_before>not_before THEN retry_not_before ELSE not_before END) FROM market_update_run WHERE market_scope=:scope AND requirements_version=:version AND status IN ('QUEUED','WAITING_RETRY')"),dict(scope=SCOPE,version=VERSION)).scalar()
            return utc(value) if value is not None else None
