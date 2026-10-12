"""Scoped insert-only preparation transactions, with one current-read per batch."""
from sqlalchemy import text,bindparam
from sqlalchemy.exc import IntegrityError

from app.market_data.repair_contract import DAILY,BASIC,canonical
from app.market_data.repair_snapshot import equal_business
from .market_repair_repository import MarketRepairRepository


class PreparationImportRepository:
    def __init__(self,session_factory,*,owner_guard=None):
        self.session_factory=session_factory
        self.owner_guard=owner_guard
        self.reader=MarketRepairRepository(session_factory)

    def capture(self,codes,start,end):
        return self.reader.capture_baseline(codes,start,end)

    @staticmethod
    def _read_targets(session,table,rows):
        suffix=' FOR UPDATE' if session.get_bind().dialect.name=='mysql' else ''
        statement=text(f'SELECT * FROM `{table}` WHERE (ts_code,trade_date) IN :keys'+suffix)
        keys=[(r['ts_code'],r['trade_date']) for r in rows]
        data=session.execute(statement.bindparams(bindparam('keys',expanding=True)),dict(keys=keys)).mappings().all()
        result={(r['ts_code'],str(r['trade_date'])):dict(r) for r in data}
        if len(result)!=len(data): raise ValueError('DATABASE_DUPLICATE_KEY')
        return result

    def insert_batch(self,table,rows,schema):
        allowed=set(DAILY if table=='stock_daily' else BASIC+['source'] if table=='stock_daily_basic' else [])
        if (not allowed or not rows or len(rows)>500
                or any(not {'ts_code','trade_date'}<=set(r)<=allowed for r in rows)
                or len({(r['ts_code'],r['trade_date']) for r in rows})!=len(rows)):
            raise ValueError('INVALID_INSERT_BATCH')
        types=schema.get('types',schema)
        result=dict(inserted=0,already_equal=0,conflicts=[])
        with self.session_factory() as session:
            try:
                if session.get_bind().dialect.name=='sqlite': session.connection().exec_driver_sql('BEGIN')
                if self.owner_guard is not None: self.owner_guard(session)
                existing=self._read_targets(session,table,rows)
                for candidate in rows:
                    key=candidate['ts_code'],str(candidate['trade_date'])
                    old=existing.get(key)
                    if old is None:
                        columns=','.join('`'+f+'`' for f in candidate)
                        params=','.join(':'+f for f in candidate)
                        try:
                            with session.begin_nested():
                                inserted=session.execute(text(f'INSERT INTO `{table}` ({columns}) VALUES ({params})'),candidate)
                                if inserted.rowcount!=1: raise ValueError('INSERT_ROWCOUNT_INVALID')
                        except IntegrityError as error:
                            code=error.orig.args[0] if error.orig.args else None
                            duplicate=(code==1062 or session.get_bind().dialect.name=='sqlite'
                                and getattr(error.orig,'sqlite_errorcode',None)==2067)
                            if not duplicate: raise
                            current=self._read_targets(session,table,[candidate])
                            old=current.get(key)
                            if old is None: raise ValueError('CONCURRENT_KEY_DISAPPEARED') from None
                        else:
                            result['inserted']+=1
                            continue
                    if equal_business(old,candidate,list(candidate),types):
                        result['already_equal']+=1
                    else:
                        session.rollback()
                        return dict(inserted=0,already_equal=0,conflicts=[dict(table=table,ts_code=key[0],trade_date=key[1])])
                session.commit()
            except Exception:
                session.rollback()
                raise
        return result

    def verify(self,baseline,candidates):
        codes=[r['ts_code'] for r in baseline['stock_basic']]
        current=self.capture(codes,baseline['start'],baseline['end'])
        MarketRepairRepository._assert_original(baseline,current)
        missing=[];conflicts=[]
        for table,rows in candidates.items():
            data={(r['ts_code'],str(r['trade_date'])):r for r in current[table]}
            for candidate in rows:
                key=candidate['ts_code'],str(candidate['trade_date'])
                old=data.get(key)
                if old is None: missing.append(dict(table=table,ts_code=key[0],trade_date=key[1]))
                elif not equal_business(old,candidate,list(candidate),baseline['schema'][table]['types']):
                    conflicts.append(dict(table=table,ts_code=key[0],trade_date=key[1]))
        return dict(db_import_complete=not missing and not conflicts,original_rows_unchanged=True,
                    missing=missing,conflicts=conflicts)
