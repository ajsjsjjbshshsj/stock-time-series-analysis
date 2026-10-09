"""Catch unsafe SDK completion, quota bypass, and invalid shard reuse."""
import importlib
import json
import pandas as pd
import pytest
from app.market_data.repair_contract import request


def module():
    try: return importlib.import_module('app.market_data.repair_acquisition')
    except ModuleNotFoundError: pytest.fail('repair acquisition is not implemented')


class Clock:
    def __init__(self): self.now=1791540000.; self.sleeps=[]
    def __call__(self): return self.now
    def sleep(self,n): self.sleeps.append(n); self.now+=n


def daily():
    return dict(ts_code='000001.SZ',trade_date='20260930',open=12.,high=13.,low=11.,close=12.5,pre_close=12.,change=.5,pct_chg=4.1,vol=123.,amount=456.)


class Client:
    def __init__(self,rows=None,error=None): self.rows=[daily()] if rows is None else rows; self.error=error; self.calls=[]
    def daily(self,**kwargs):
        self.calls.append(kwargs)
        if self.error: raise RuntimeError(self.error)
        return pd.DataFrame(self.rows,columns=request('daily',['000001.SZ'],'2026-09-30','2026-09-30')['fields'])


def collect(tmp_path,client,clock=None,requests=None,output=None):
    clock=clock or Clock()
    req=requests or [request('daily',['000001.SZ'],'2026-09-30','2026-09-30')]
    return module().collect_requests(client,req,output or tmp_path/'acq',tmp_path/'state.json',.5,clock,clock.sleep)


def test_valid_shard_resume_is_readonly_without_client(tmp_path):
    first=collect(tmp_path,Client()); assert first['complete']
    before={p.name:p.read_bytes() for p in (tmp_path/'acq').rglob('*') if p.is_file()}
    assert collect(tmp_path,None)['complete']
    assert {p.name:p.read_bytes() for p in (tmp_path/'acq').rglob('*') if p.is_file()}==before


@pytest.mark.parametrize('error,calls,deadline',[('connection timeout SECRET',3,0),('token SECRET 权限',1,0),('每分钟超过限制 SECRET',1,61),('每小时限制 SECRET',1,3601)])
def test_errors_are_bounded_and_secret_safe(tmp_path,error,calls,deadline):
    c=Client(error=error); clock=Clock(); report=collect(tmp_path,c,clock)
    assert not report['complete'] and len(c.calls)==calls
    assert 'SECRET' not in json.dumps(report)
    assert all('SECRET' not in p.read_text(encoding='utf-8') for p in tmp_path.rglob('*.json'))
    if deadline:
        state=json.loads((tmp_path/'state.json').read_text())
        assert state['retry_not_before']==clock()+deadline
        other=Client(); blocked=collect(tmp_path,other,clock,output=tmp_path/'other')
        assert not blocked['complete'] and not other.calls


@pytest.mark.parametrize('bad',['duplicate','date','code','zero','inf','range','columns'])
def test_invalid_response_retained_but_never_complete(tmp_path,bad):
    row=daily(); rows=[row]
    if bad=='duplicate': rows*=2
    if bad=='date': row['trade_date']='20261009'
    if bad=='code': row['ts_code']='000002.SZ'
    if bad=='zero': row['close']=0.
    if bad=='inf': row['amount']=float('inf')
    if bad=='range': row['low']=20.
    if bad=='columns': row.pop('open')
    report=collect(tmp_path,Client(rows))
    assert not report['complete']
    assert not (tmp_path/'acq/complete.json').exists()


def test_empty_response_is_evidence_not_suspension(tmp_path):
    assert collect(tmp_path,Client([]))['complete']
    r=[request('daily',['000001.SZ'],'2026-09-30','2026-09-30')]
    verified=module().verify_acquisition(tmp_path/'acq',r)
    assert verified['shards'][0]['rows']==[]


def test_hash_tamper_refuses_cached_shard(tmp_path):
    collect(tmp_path,Client())
    shard=next((tmp_path/'acq/shards').glob('*.json'))
    shard.write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError): collect(tmp_path,None)


def test_capacity_boundary_cannot_publish_complete(tmp_path):
    req=request('daily',['000001.SZ'],'2026-09-30','2026-09-30'); req['capacity']=1
    # A smaller capacity represents provider truncation in a deterministic test.
    from app.market_data.repair_contract import identity
    req['id']=identity({k:v for k,v in req.items() if k!='id'})
    report=collect(tmp_path,Client(),requests=[req])
    assert not report['complete']


def test_cross_stage_pacing_uses_shared_state(tmp_path):
    clock=Clock(); collect(tmp_path,Client(),clock)
    collect(tmp_path,Client(),clock,output=tmp_path/'other')
    assert clock.sleeps[-1]==.5


def test_native_basic_null_and_external_stock_preserved(tmp_path):
    req=request('daily_basic',['000001.SZ'],'2026-09-30','2026-09-30')
    rows=[dict(ts_code=c,trade_date='20260930',turnover_rate=1.,pe=None,pe_ttm=None,pb=2.,ps=3.,total_mv=100.) for c in ['000001.SZ','600999.SH']]
    class Basic:
        def daily_basic(self,**kwargs): return pd.DataFrame(rows)
    assert collect(tmp_path,Basic(),requests=[req])['complete']
    data=module().verify_acquisition(tmp_path/'acq',[req])['shards'][0]['rows']
    assert len(data)==2 and data[0]['pe'] is None
