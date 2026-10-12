"""Small hand-built stock/day market fixtures, no real SDK or database access."""
from app.preparation.contracts import freeze_pool
from app.market_data.repair_contract import DAILY, BASIC


def fixture():
    pool = freeze_pool(dict(pool_id='industry:银行', display_name='银行', group_type='industry',
        group_code='银行', provider='akshare.eastmoney', requested_as_of='2026-10-11',
        provider_snapshot_date=None, acquired_at='2026-10-11T17:00:00+08:00'),
        [{'ts_code':'000001.SZ'}, {'ts_code':'600000.SH'}])
    days = ['2026-09-28','2026-09-29','2026-09-30']
    stocks = [dict(ts_code=c, list_date='1991-04-03') for c in pool['codes']]
    quotes, basic, factors = [], [], []
    for c in pool['codes']:
        for d in days:
            quotes.append(dict(id=len(quotes)+1, ts_code=c,trade_date=d, open='10',high='11',low='9',
                close='10.5',pre_close='10',change='0.5',pct_chg='5',vol='100',amount='1000',created_at='2026-10-01T00:00:00'))
            basic.append(dict(id=len(basic)+1,ts_code=c,trade_date=d,turnover_rate='1',pe=None,
                pe_ttm=None,pb='1',ps='2',total_mv='10000',source='TUSHARE',created_at='2026-10-01T00:00:00',updated_at='2026-10-01T00:00:00'))
            factors.append(dict(ts_code=c,trade_date=d,adj_factor=1.0))
    tables = dict(start=days[0],end=days[-1],stock_daily=quotes,stock_daily_basic=basic,stock_basic=stocks,
        schema={t:dict(columns=list(rows[0]),types={c:'decimal(20,6)' for c in fields[2:]},
            unique_keys=[['ts_code','trade_date']]) for t,rows,fields in
            [('stock_daily',quotes,DAILY),('stock_daily_basic',basic,BASIC)]})
    window = dict(start=days[0],end=days[-1],requested_start=days[0],requested_end=days[-1],cutoff_reason=None,empty=False)
    evidence = dict(calendar=dict(source='tushare.trade_cal', rows=[dict(cal_date=d,is_open=1) for d in days]),
        daily=[], daily_basic=[], adj_factor=factors, suspend_d=[], queried_suspensions=[])
    return pool,window,tables,evidence


def build(*args):
    import importlib
    import pytest
    try: function=importlib.import_module('app.preparation.inventory').build_inventory
    except ModuleNotFoundError: pytest.fail('generic inventory not implemented')
    return function(*args)


def plan(inventory):
    import importlib
    import pytest
    try: function=importlib.import_module('app.preparation.planner').plan_missing
    except ModuleNotFoundError: pytest.fail('generic gap planner not implemented')
    return function(inventory)
