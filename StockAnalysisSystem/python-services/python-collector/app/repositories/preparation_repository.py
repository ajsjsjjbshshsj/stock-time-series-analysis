"""Persisted task identity, atomic claim and owner-fenced status changes."""
import json
import re
import uuid

from sqlalchemy import text

from app.preparation.contracts import identity, iso
from ._preparation_sql import transaction, encode, stamp, utc, current_lock

ACTIVE = {'QUEUED','RUNNING','WAITING_RETRY'}
TERMINAL = {'SUCCEEDED','PARTIAL','FAILED'}
PHASES = {'QUEUED','RESOLVE','INVENTORY','ACQUIRE','ASSEMBLE','IMPORT','VERIFY'}
ERRORS = {'NETWORK','PERMISSION','SCHEMA','PROVIDER','MINUTE_QUOTA','HOURLY_QUOTA',
          'QUOTA_COOLDOWN','REQUEST_STATE_BUSY','SOURCE_CONFLICT','INVALID_EXISTING_DATA',
          'RECOVERY_CONFIRMATION_REQUIRED','ORIGINAL_CHANGED','TRANSACTION_FAILED','STORAGE_UNAVAILABLE'}
PATCH_FIELDS = {'status','phase','progress','result','error_kind','retry_not_before','updated_at'}


def validate_request(request):
    required={'pool_version','requested_start','requested_end','requirements_version'}
    if (not required<=set(request)<=required|{'cutoff_date'}
            or request['requirements_version'] != 'market-v1'
            or type(request['pool_version']) is not str
            or re.fullmatch(r'(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})', request['pool_version']) is None
            or iso(request['requested_start']) > iso(request['requested_end'])):
        raise ValueError('PREPARATION_REQUEST_INVALID')
    if 'cutoff_date' in request and iso(request['cutoff_date'])>iso(request['requested_end']):
        raise ValueError('PREPARATION_CUTOFF_INVALID')
    return request


def _task(row):
    value = dict(row)
    for column, target in (('request_json','request'),('owner_json','owner'),
                           ('progress_json','progress'),('result_json','result')):
        data = value.pop(column)
        value[target] = json.loads(data) if data is not None else None
    for key in ('created_at','updated_at','lease_until','retry_not_before'):
        value[key] = utc(value[key])
    validate_request(value['request'])
    if value['identity'] != identity(value['request']) or value['status'] not in ACTIVE|TERMINAL:
        raise ValueError('TASK_IDENTITY_CHANGED')
    return value


class PreparationRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @staticmethod
    def _get(session, task_id, *, lock=False):
        if type(task_id) is not str or re.fullmatch('[0-9a-f]{32}',task_id) is None:
            raise ValueError('TASK_ID_INVALID')
        row = session.execute(text('SELECT * FROM data_preparation_task WHERE task_id=:id'
                                   + (current_lock(session) if lock else '')), dict(id=task_id)).mappings().first()
        if row is None: raise ValueError('TASK_NOT_FOUND')
        return _task(row)

    def get(self, task_id):
        with transaction(self.session_factory) as session:
            return self._get(session,task_id)

    def fence(self,task_id,owner_token,*,now,session=None):
        def check(current):
            task=self._get(current,task_id,lock=True)
            if (task['status']!='RUNNING' or task['owner_token']!=owner_token
                    or task['lease_until'] is None or stamp(task['lease_until'])<=stamp(now)):
                raise RuntimeError('OWNER_EXPIRED_OR_CHANGED')
            return task
        if session is not None: return check(session)
        with transaction(self.session_factory) as current: return check(current)

    def expired(self,*,now):
        with transaction(self.session_factory) as session:
            rows=session.execute(text("SELECT * FROM data_preparation_task WHERE status='RUNNING' AND lease_until<=:now"),
                                 dict(now=stamp(now))).mappings().all()
            return [_task(r) for r in rows]

    def recover(self,task_id,old_owner_token,*,now):
        # Called only after the worker independently proves the old process dead.
        with transaction(self.session_factory) as session:
            result=session.execute(text("UPDATE data_preparation_task SET status='QUEUED',owner_token=NULL,"
                'owner_json=NULL,lease_until=NULL,updated_at=:now '
                "WHERE task_id=:id AND owner_token=:token AND status='RUNNING' AND lease_until<=:now"),
                dict(id=task_id,token=old_owner_token,now=stamp(now)))
            return result.rowcount==1

    def next_retry(self):
        with transaction(self.session_factory) as session:
            value=session.execute(text("SELECT MIN(retry_not_before) FROM data_preparation_task WHERE status='WAITING_RETRY'")).scalar()
            return utc(value)

    def submit(self, request, *, now):
        validate_request(request)
        clock = stamp(now)
        task_identity = identity(request)
        with transaction(self.session_factory) as session:
            # Serialize same-pool submissions before the active unique constraint.
            version = session.execute(text('SELECT pool_version FROM stock_pool_version WHERE pool_version=:v'
                + current_lock(session)),dict(v=request['pool_version'])).first()
            if version is None: raise ValueError('POOL_NOT_FOUND')
            row = session.execute(text('SELECT * FROM data_preparation_task WHERE active_identity=:i'),
                                  dict(i=task_identity)).mappings().first()
            if row: return _task(row)
            task_id = uuid.uuid4().hex
            session.execute(text('INSERT INTO data_preparation_task '
                '(task_id,identity,active_identity,request_json,status,phase,progress_json,created_at,updated_at) '
                'VALUES (:id,:i,:i,:request,:status,:phase,:progress,:now,:now)'),
                dict(id=task_id,i=task_identity,request=encode(request),status='QUEUED',phase='QUEUED',
                     progress=encode({}),now=clock))
            return self._get(session,task_id)

    def claim(self, owner, *, now, lease_until):
        clock, until = stamp(now), stamp(lease_until)
        token = owner.get('token')
        if (until <= clock or type(token) is not str or re.fullmatch('[0-9a-f]{32}',token) is None
                or type(owner.get('pid')) is not int or owner['pid'] <= 0):
            raise ValueError('OWNER_OR_LEASE_INVALID')
        stamp(owner['started_at'])
        with transaction(self.session_factory) as session:
            row = session.execute(text("SELECT task_id FROM data_preparation_task WHERE status IN ('QUEUED','WAITING_RETRY') "
                'AND (retry_not_before IS NULL OR retry_not_before<=:now) ORDER BY created_at,task_id LIMIT 1'
                + current_lock(session)),dict(now=clock)).first()
            if row is None: return None
            result = session.execute(text("UPDATE data_preparation_task SET status='RUNNING',owner_token=:token,"
                'owner_json=:owner,lease_until=:until,updated_at=:now '
                "WHERE task_id=:id AND status IN ('QUEUED','WAITING_RETRY') "
                'AND (retry_not_before IS NULL OR retry_not_before<=:now)'),
                dict(id=row[0],token=token,owner=encode(owner),until=until,now=clock))
            return self._get(session,row[0]) if result.rowcount == 1 else None

    def heartbeat(self, task_id, owner_token, *, now, lease_until):
        clock, until = stamp(now), stamp(lease_until)
        if until <= clock: raise ValueError('LEASE_INVALID')
        with transaction(self.session_factory) as session:
            result = session.execute(text('UPDATE data_preparation_task SET lease_until=:until,updated_at=:now '
                "WHERE task_id=:id AND owner_token=:token AND status='RUNNING' AND lease_until>:now"),
                dict(id=task_id,token=owner_token,until=until,now=clock))
            return result.rowcount == 1

    def update(self, task_id, owner_token, patch):
        if not patch or not set(patch) <= PATCH_FIELDS or 'updated_at' not in patch:
            raise ValueError('TASK_PATCH_INVALID')
        if ('status' in patch and patch['status'] not in {'RUNNING','WAITING_RETRY'}|TERMINAL
                or 'phase' in patch and patch['phase'] not in PHASES
                or patch.get('error_kind') is not None and patch['error_kind'] not in ERRORS):
            raise ValueError('TASK_STATE_INVALID')
        if patch.get('status') == 'WAITING_RETRY' and patch.get('retry_not_before') is None:
            raise ValueError('RETRY_DEADLINE_REQUIRED')
        for k in ('progress','result'):
            if k in patch and patch[k] is not None and not isinstance(patch[k],dict):
                raise ValueError('TASK_PAYLOAD_INVALID')
        values = {}
        for k,v in patch.items():
            if k in ('progress','result'): values[k+'_json'] = encode(v) if v is not None else None
            elif k in ('updated_at','retry_not_before'): values[k] = stamp(v) if v is not None else None
            else: values[k] = v
        target = patch.get('status')
        if target in TERMINAL:
            values.update(active_identity=None,owner_token=None,owner_json=None,lease_until=None)
        elif target == 'WAITING_RETRY':
            values.update(owner_token=None,owner_json=None,lease_until=None)
        sql = ','.join(k+'=:'+k for k in values)
        params = dict(values, id=task_id, token=owner_token, now=values['updated_at'])
        with transaction(self.session_factory) as session:
            result = session.execute(text('UPDATE data_preparation_task SET '+sql+
                " WHERE task_id=:id AND owner_token=:token AND status='RUNNING' AND lease_until>:now"),params)
            return result.rowcount == 1

    def retry(self, task_id, *, now):
        clock = stamp(now)
        with transaction(self.session_factory) as session:
            task = self._get(session,task_id,lock=True)
            if task['status'] not in {'FAILED','PARTIAL'}:
                raise ValueError('ONLY_FAILED_OR_PARTIAL_CAN_RETRY')
            active = session.execute(text('SELECT task_id FROM data_preparation_task WHERE active_identity=:i'),
                                     dict(i=task['identity'])).first()
            if active: raise ValueError('ACTIVE_TASK_EXISTS')
            session.execute(text("UPDATE data_preparation_task SET status='QUEUED',active_identity=:i,"
                'retry_not_before=NULL,error_kind=NULL,updated_at=:now WHERE task_id=:id'),
                dict(i=task['identity'],now=clock,id=task_id))
            return self._get(session,task_id)
