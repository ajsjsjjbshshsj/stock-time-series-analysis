"""Fetch missing IPO identities, inserting new stocks without updating old info."""
from pathlib import Path
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.market_data.forward_snapshot import write,digest,read
from .contracts import CODE,identity,iso
from .acquisition import source_failure
from .service import PreparationFailure

FIELDS=('ts_code','symbol','name','area','industry','list_date')


class ReferenceLoader:
    def __init__(self,client_factory,requests_factory,session_factory):
        self.client_factory=client_factory; self.requests_factory=requests_factory
        self.session_factory=session_factory
        self.owner_guard=None

    def ensure(self,pool,codes,directory):
        if not set(codes)<=set(pool['codes']): raise ValueError('REFERENCE_OUTSIDE_POOL')
        needed=set(codes)
        # Recheck before touching the source, including a concurrent pool's work.
        with self.session_factory() as session:
            for code in list(needed):
                row=session.execute(text('SELECT list_date FROM stock_basic WHERE ts_code=:c'),dict(c=code)).first()
                if row:
                    if row[0] is None: raise PreparationFailure('INVALID_EXISTING_DATA')
                    iso(str(row[0])); needed.remove(code)
        if not needed: return
        folder=Path(directory)/'reference'; folder.mkdir(parents=True,exist_ok=True)
        pacing=self.requests_factory(folder); client=None
        available={}
        for status in ('L','D','P'):
            path=folder/(status+'.json'); proof=path.with_suffix('.proof.json')
            if path.exists():
                if not proof.exists() or read(proof)!=dict(sha256=digest(path)): raise ValueError('REFERENCE_HASH_INVALID')
                rows=read(path)['rows']
            else:
                if client is None: client=self.client_factory()
                try: frame=pacing.call(client.stock_basic,dict(list_status=status,fields=','.join(FIELDS)))
                except RuntimeError: raise source_failure(pacing.state) from None
                if frame is None or frame.columns.duplicated().any() or set(frame.columns)!=set(FIELDS) or len(frame)>=6000:
                    raise ValueError('REFERENCE_RESPONSE_SCHEMA')
                rows=frame.astype(object).where(frame.notna(),None).to_dict('records')
                self._validate(rows)
                write(path,dict(source='tushare.stock_basic',list_status=status,rows=rows),exclusive=True)
                write(proof,dict(sha256=digest(path)),exclusive=True)
            self._validate(rows)
            for row in rows:
                if row['ts_code'] not in needed: continue
                normalized=dict(row,list_date=datetime.strptime(row['list_date'],'%Y%m%d').date().isoformat())
                if row['ts_code'] in available and available[row['ts_code']]!=normalized:
                    raise PreparationFailure('SOURCE_CONFLICT')
                available[row['ts_code']]=normalized
            if needed<=set(available): break
        if needed-set(available): raise PreparationFailure('INVALID_EXISTING_DATA')
        with self.session_factory.begin() as session:
            if self.owner_guard is not None: self.owner_guard(session)
            for code in sorted(needed):
                suffix=' FOR UPDATE' if session.get_bind().dialect.name=='mysql' else ''
                old=session.execute(text('SELECT list_date FROM stock_basic WHERE ts_code=:c'+suffix),dict(c=code)).first()
                if old:
                    if old[0] is None: raise PreparationFailure('INVALID_EXISTING_DATA')
                    continue
                try:
                    with session.begin_nested():
                        session.execute(text('INSERT INTO stock_basic (ts_code,symbol,name,area,industry,list_date) '
                            'VALUES (:ts_code,:symbol,:name,:area,:industry,:list_date)'),available[code])
                except IntegrityError as error:
                    duplicate=(error.orig.args and error.orig.args[0]==1062) or session.get_bind().dialect.name=='sqlite' and getattr(error.orig,'sqlite_errorcode',None)==2067
                    if not duplicate: raise
                    old=session.execute(text('SELECT list_date FROM stock_basic WHERE ts_code=:c'+suffix),dict(c=code)).first()
                    if old is None or old[0] is None: raise PreparationFailure('SOURCE_CONFLICT') from None

    @staticmethod
    def _validate(rows):
        seen=set()
        for row in rows:
            if set(row)!=set(FIELDS) or type(row['ts_code']) is not str or CODE.fullmatch(row['ts_code']) is None:
                raise ValueError('REFERENCE_IDENTITY')
            if row['ts_code'] in seen: raise ValueError('REFERENCE_DUPLICATE')
            seen.add(row['ts_code'])
            if type(row['list_date']) is not str or len(row['list_date'])!=8:
                raise ValueError('REFERENCE_LIST_DATE')
            datetime.strptime(row['list_date'],'%Y%m%d')
            for key,limit in (('symbol',10),('name',50),('area',20),('industry',30)):
                value=row[key]
                if value is not None and (type(value) is not str or len(value)>limit):
                    raise ValueError('REFERENCE_FIELD')
