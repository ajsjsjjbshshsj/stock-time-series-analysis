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
        validate_ref(ref)
        binding = {k:ref[k] for k in ('kind','ts_code','start_date','end_date','content_sha256')}
        payload = dict(ref, evidence_id=identity(binding))
        with transaction(self.session_factory) as session:
            existing = session.execute(text('SELECT * FROM market_source_evidence WHERE evidence_id=:e'),
                                       dict(e=payload['evidence_id'])).mappings().first()
            if existing:
                actual = dict(existing)
                for k in ('start_date', 'end_date'): actual[k] = str(actual[k])
                if actual != payload:
                    raise ValueError('EVIDENCE_REFERENCE_CONFLICT')
                return actual
            columns = ','.join(('evidence_id',) + FIELDS)
            params = ','.join(':'+k for k in ('evidence_id',) + FIELDS)
            session.execute(text(f'INSERT INTO market_source_evidence ({columns}) VALUES ({params})'), payload)
        return payload

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
