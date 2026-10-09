"""Catch false gap classification and destructive baseline reuse."""
import importlib
import json
from pathlib import Path
from decimal import Decimal

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parents[4]
POOL = None


@pytest.fixture(autouse=True)
def portable_pool(tmp_path,monkeypatch):
    """Build real sealed evidence from a synthetic provider; no local models needed."""
    from app.market_data.csi300_constituent_snapshot import collect_constituent_evidence
    from app.market_data.repair_contract import verify_pool
    import importlib.util
    spec=importlib.util.spec_from_file_location('_test_pool',ROOT/'StockAnalysisSystem/python-services/stock-analysis-app/analysis/csi300_universe.py')
    mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    fields=['index_code','con_code','trade_date','weight']
    class Client:
        def index_weight(self,**kwargs):
            rows=[] if kwargs['start_date']=='20261001' else [dict(index_code='000300.SH',con_code=f'{i:06d}.SZ',trade_date='20260930',weight=.25) for i in range(1,301)]
            return pd.DataFrame(rows,columns=fields)
    epoch=datetime(2026,10,9,18,tzinfo=timezone(timedelta(hours=8))).timestamp()
    acq=tmp_path/'constituents'
    collect_constituent_evidence(Client(),'2026-10-09',acq,request_state=tmp_path/'request_state.json',clock=lambda:epoch,sleep=lambda _:None)
    src=mod.read_acquisition(acq)
    version=mod.build_universe(src,src['rows'],'2026-10-09T18:00:01+08:00')['pool_version']
    pool=tmp_path/'csi300_fixed'/version
    mod.publish_universe(acq,pool,'2026-10-09T18:00:01+08:00')
    monkeypatch.setitem(globals(),'POOL',pool)


def module():
    try:
        return importlib.import_module('app.market_data.repair_contract')
    except ModuleNotFoundError:
        pytest.fail('repair baseline contract is not implemented')


def inputs():
    codes = [f'{i:06d}.SZ' for i in range(1,301)]
    stock = [dict(ts_code=c, list_date='2000-01-01') for c in codes]
    stock[-1]['list_date'] = '2023-10-11'
    daily = [dict(id=1, ts_code=codes[0], trade_date='2023-10-09', close=Decimal('12.123456'), created_at='2023-10-09T17:00:00')]
    basic = [dict(id=1, ts_code=codes[0], trade_date='2023-10-09', pe=None)]
    schema = {t: dict(columns=list(rows[0]), types={k:'TEXT' for k in rows[0]}, unique_keys=[['ts_code','trade_date']]) for t,rows in [('stock_daily',daily),('stock_daily_basic',basic)]}
    calendar = dict(source='tushare.trade_cal', start='2023-10-09', end='2023-10-11', rows=[dict(cal_date=d,is_open=1) for d in ['2023-10-09','2023-10-10','2023-10-11']])
    return calendar, dict(start='2023-10-09',end='2023-10-11',stock_basic=stock,stock_daily=daily,stock_daily_basic=basic,schema=schema)


def baseline(tmp_path):
    cal,tables = inputs()
    module().write_baseline(tmp_path/'baseline',POOL,cal,tables)
    return tmp_path/'baseline'


def test_common_gap_is_requested_not_assumed_suspension(tmp_path):
    plan = module().plan_requests(baseline(tmp_path))
    first = next(r for r in plan if r['method']=='daily' and r['start']=='2023-10-09')
    assert len(first['expected_codes']) == 298  # one observed, one not yet listed
    assert all(r['end'] <= '2023-10-11' for r in plan)


def test_existing_basic_with_null_pe_is_not_missing(tmp_path):
    plan = module().plan_requests(baseline(tmp_path))
    assert not [r for r in plan if r['method']=='daily_basic']


def test_baseline_preserves_decimal_and_timestamp_and_is_readonly(tmp_path):
    m = module(); path = baseline(tmp_path)
    before = {p.name:p.read_bytes() for p in path.rglob('*') if p.is_file()}
    cal,tables=inputs(); m.write_baseline(path,POOL,cal,tables)
    assert {p.name:p.read_bytes() for p in path.rglob('*') if p.is_file()} == before
    data=m.load_baseline(path)
    assert len(data['pool']['codes']) == 300
    assert data['tables']['stock_daily'][0]['close']=='12.123456'
    assert data['tables']['stock_daily'][0]['created_at']=='2023-10-09T17:00:00'


@pytest.mark.parametrize('kind',['calendar','ipo','schema','duplicate'])
def test_invalid_baseline_never_completes(tmp_path,kind):
    m=module(); cal,tables=inputs()
    if kind=='calendar': cal['rows'].pop()
    if kind=='ipo': tables['stock_basic'].pop()
    if kind=='schema': tables['schema']['stock_daily']['unique_keys']=[]
    if kind=='duplicate': tables['stock_daily'] *= 2
    with pytest.raises(ValueError): m.write_baseline(tmp_path/'bad',POOL,cal,tables)
    assert not (tmp_path/'bad/complete.json').exists()


def test_hash_tampering_is_rejected(tmp_path):
    path=baseline(tmp_path)
    (path/'tables.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError): module().verify_baseline(path)


def test_prelisting_members_not_requested_for_factors(tmp_path):
    plan=module().plan_requests(baseline(tmp_path))
    late=inputs()[1]['stock_basic'][-1]['ts_code']
    request=next(r for r in plan if r['method']=='adj_factor' and r['expected_codes']==[late])
    assert request['start']=='2023-10-11'
