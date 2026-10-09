"""Dedicated repair repository: separate table inventory, no legacy upserts."""
from sqlalchemy import bindparam, text


class MarketRepairRepository:
    def __init__(self,session_factory):
        self.session_factory=session_factory

    def capture_baseline(self,codes: list[str],start: str,end: str) -> dict:
        if not codes or len(codes)!=len(set(codes)): raise ValueError('Distinct stock identities required')
        result=dict(start=start,end=end,schema={})
        with self.session_factory() as session:
            for table in ('stock_daily','stock_daily_basic'):
                # Identifiers are fixed literals; all external values are bound.
                columns=session.execute(text('SELECT COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY ORDINAL_POSITION'),dict(table=table)).mappings().all()
                indexes=session.execute(text('SELECT INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX,NON_UNIQUE FROM information_schema.STATISTICS '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY INDEX_NAME,SEQ_IN_INDEX'),dict(table=table)).mappings().all()
                unique={}
                for r in indexes:
                    if not r['NON_UNIQUE']: unique.setdefault(r['INDEX_NAME'],[]).append(r['COLUMN_NAME'])
                result['schema'][table]=dict(columns=[r['COLUMN_NAME'] for r in columns],
                    types={r['COLUMN_NAME']:r['COLUMN_TYPE'] for r in columns},unique_keys=list(unique.values()))
                stmt=text(f'SELECT * FROM `{table}` WHERE ts_code IN :codes AND trade_date BETWEEN :start AND :end '
                    'ORDER BY ts_code,trade_date').bindparams(bindparam('codes',expanding=True))
                result[table]=[dict(r) for r in session.execute(stmt,dict(codes=codes,start=start,end=end)).mappings().all()]
            stmt=text('SELECT * FROM stock_basic WHERE ts_code IN :codes ORDER BY ts_code').bindparams(bindparam('codes',expanding=True))
            result['stock_basic']=[dict(r) for r in session.execute(stmt,dict(codes=codes)).mappings().all()]
        return result
