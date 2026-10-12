"""Repository boundaries: real query construction with a read-only session double."""
import importlib
from datetime import date
from decimal import Decimal

import pytest


def test_capture_reads_separate_tables_and_preserves_decimals():
    try: cls=importlib.import_module('app.repositories.market_repair_repository').MarketRepairRepository
    except ModuleNotFoundError: pytest.fail('repair repository is not implemented')
    queries=[]
    class Result:
        def __init__(self,rows): self.rows=rows
        def mappings(self): return self
        def all(self): return self.rows
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,stmt,params=None):
            sql=str(stmt); queries.append(sql)
            assert sql.lstrip().upper().startswith('SELECT')
            assert 'JOIN' not in sql.upper()
            if 'information_schema.COLUMNS' in sql:
                return Result([dict(COLUMN_NAME=c,COLUMN_TYPE=t) for c,t in [('id','bigint'),('ts_code','varchar(10)'),('trade_date','date'),('close','decimal(20,6)')]])
            if 'information_schema.STATISTICS' in sql:
                return Result([dict(INDEX_NAME='uq',COLUMN_NAME=c,SEQ_IN_INDEX=i,NON_UNIQUE=0) for i,c in enumerate(['ts_code','trade_date'],1)])
            if 'stock_basic' in sql: return Result([dict(ts_code='000001.SZ',list_date=date(1991,4,3))])
            return Result([dict(id=1,ts_code='000001.SZ',trade_date=date(2023,10,9),close=Decimal('12.123456'))])
    result=cls(Session).capture_baseline(['000001.SZ'],'2023-10-09','2023-10-11')
    assert result['stock_daily'][0]['close']==Decimal('12.123456')
    assert result['schema']['stock_daily']['unique_keys']==[['ts_code','trade_date']]
    assert len(queries)==7
