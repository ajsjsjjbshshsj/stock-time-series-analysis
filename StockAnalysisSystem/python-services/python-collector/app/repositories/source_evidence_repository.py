"""Evidence references are a lookup index, never proof that a file is valid."""
from pathlib import PurePosixPath
import re

from sqlalchemy import text, bindparam

from app.preparation.contracts import CODE, identity, iso
from ._preparation_sql import transaction

FIELDS = ('kind', 'ts_code', 'start_date', 'end_date', 'provider', 'relative_path',
          'manifest_sha256', 'content_sha256')
PROVIDERS = {'adj_factor':'tushare.adj_factor', 'suspend_d':'tushare.suspend_d',
             'daily':'tushare.daily', 'daily_basic':'tushare.daily_basic', 'trade_cal':'tushare.trade_cal'}


def validate_ref(ref):
    if set(ref) != set(FIELDS):
        raise ValueError('EVIDENCE_FIELDS')
    if (ref['kind'] not in PROVIDERS or ref['provider'] != PROVIDERS[ref['kind']]
            or type(ref['ts_code']) is not str or CODE.fullmatch(ref['ts_code']) is None
            or iso(ref['start_date']) > iso(ref['end_date'])):
        raise ValueError('EVIDENCE_IDENTITY')
    path = ref['relative_path']
    if (not isinstance(path, str) or not path or len(path)>512 or ':' in path or '\\' in path
            or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts):
        raise ValueError('EVIDENCE_PATH')
    if any(type(ref[k]) is not str or re.fullmatch('[0-9a-f]{64}', ref[k]) is None
           for k in ('manifest_sha256', 'content_sha256')):
        raise ValueError('EVIDENCE_HASH')
    return ref


class SourceEvidenceRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def register(self, ref: dict) -> dict:
        return self.register_many([ref])[0]

    def register_many(self, refs: list[dict], *, owner_guard=None) -> list[dict]:
        payloads={}
        for ref in refs:
            validate_ref(ref)
            binding={k:ref[k] for k in ('kind','ts_code','start_date','end_date','content_sha256')}
            payload=dict(ref,evidence_id=identity(binding))
            key=payload['evidence_id']
            if key in payloads and payloads[key]!=payload: raise ValueError('EVIDENCE_REFERENCE_CONFLICT')
            payloads[key]=payload
        if not payloads: return []
        keys=list(payloads)
        columns=','.join(('evidence_id',)+FIELDS)
        params=','.join(':'+k for k in ('evidence_id',)+FIELDS)
        with transaction(self.session_factory) as session:
            if owner_guard is not None:owner_guard(session)
            for offset in range(0,len(keys),500):
                batch=keys[offset:offset+500]
                stmt=text('SELECT * FROM market_source_evidence WHERE evidence_id IN :keys').bindparams(bindparam('keys',expanding=True))
                existing=session.execute(stmt,dict(keys=batch)).mappings().all()
                present=set()
                for r in existing:
                    actual=dict(r)
                    for k in ('start_date','end_date'): actual[k]=str(actual[k])
                    if actual!=payloads[actual['evidence_id']]: raise ValueError('EVIDENCE_REFERENCE_CONFLICT')
                    present.add(actual['evidence_id'])
                missing=[payloads[k] for k in batch if k not in present]
                if missing: session.execute(text(f'INSERT INTO market_source_evidence ({columns}) VALUES ({params})'),missing)
        return list(payloads.values())

    def list(self, kind: str, codes: list[str], start: str, end: str) -> list[dict]:
        if kind not in PROVIDERS or iso(start)>iso(end) or not codes or any(CODE.fullmatch(c) is None for c in codes):
            raise ValueError('EVIDENCE_QUERY')
        stmt = text('SELECT * FROM market_source_evidence WHERE kind=:kind AND ts_code IN :codes '
                    'AND start_date<=:end AND end_date>=:start ORDER BY ts_code,start_date,evidence_id')
        with transaction(self.session_factory) as session:
            rows = session.execute(stmt.bindparams(bindparam('codes', expanding=True)),
                                   dict(kind=kind,codes=codes,start=start,end=end)).mappings().all()
            result = []
            for r in rows:
                row = dict(r)
                for k in ('start_date','end_date'): row[k] = str(row[k])
                validate_ref({k:row[k] for k in FIELDS})
                if row['evidence_id'] != identity({k:row[k] for k in ('kind','ts_code','start_date','end_date','content_sha256')}):
                    raise ValueError('EVIDENCE_INDEX_CHANGED')
                result.append(row)
            return result
