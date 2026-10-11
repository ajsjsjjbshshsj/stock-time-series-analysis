"""Checked real client + real pacing/cache; fake only remote HTTP responses."""
import json
from pathlib import Path

import pytest

from app.market_data.forward_snapshot import Requests
from app.market_data.tushare_client import TushareClient
from app.market_data.repair_acquisition import collect_requests
from app.market_data.repair_contract import request
from tests.test_checked_tushare_api import Session,response
from tests.test_market_repair_acquisition import Clock


def pacing(path,clock,shared):
    path.mkdir(exist_ok=True)
    return Requests(path,.5,clock,clock.sleep,shared)


def test_http_503_uses_one_pacing_retry_layer_three_actual_attempts(tmp_path,capsys):
    clock=Clock(); session=Session(*[response(503,raw='SECRET HTML') for _ in range(3)])
    client=TushareClient.from_token('SECRET',session=session)
    with pytest.raises(RuntimeError) as caught:
        pacing(tmp_path/'first',clock,tmp_path/'shared.json').call(client.daily,dict(ts_code='000001.SZ'))
    state=json.loads((tmp_path/'first/acquisition.json').read_text())
    assert len(session.calls)==3 and state['attempts']==3 and state['failure']=='NETWORK' and state['http_status']==503
    assert 'SECRET' not in str(caught.value) and 'SECRET' not in (tmp_path/'shared.json').read_text()
    assert 'SECRET' not in capsys.readouterr().out


def test_http_error_then_success_clears_old_status_and_returns_data(tmp_path):
    clock=Clock(); session=Session(response(503),response())
    client=TushareClient.from_token('SECRET',session=session)
    result=pacing(tmp_path/'first',clock,tmp_path/'shared.json').call(client.daily,dict(ts_code='000001.SZ'))
    state=json.loads((tmp_path/'first/acquisition.json').read_text())
    assert result.ts_code.tolist()==['000001.SZ'] and len(session.calls)==2
    assert not {'http_status','failure','retry_not_before'} & set(state)


@pytest.mark.parametrize('msg,kind,cooldown',[
    ('每分钟最多访问，权限详情 SECRET','MINUTE_QUOTA',61),
    ('每小时访问额度，Token SECRET 权限详情','HOURLY_QUOTA',3601)])
def test_http_quota_binds_canonical_deadline_across_stages(tmp_path,msg,kind,cooldown):
    clock=Clock(); session=Session(response(429,{'code':-1,'msg':msg,'data':None}))
    client=TushareClient.from_token('SECRET',session=session)
    with pytest.raises(RuntimeError): pacing(tmp_path/'first',clock,tmp_path/'shared.json').call(client.daily,{})
    state=json.loads((tmp_path/'shared.json').read_text())
    assert len(session.calls)==1 and state['failure']==kind and state['http_status']==429
    assert state['retry_not_before']==clock()+cooldown
    second=Session(response())
    with pytest.raises(RuntimeError): pacing(tmp_path/'second',clock,tmp_path/'shared.json').call(TushareClient.from_token('SECRET',session=second).daily,{})
    assert not second.calls


@pytest.mark.parametrize('status,raw,want',[(401,'SECRET','PERMISSION'),(403,'SECRET','PERMISSION'),(200,'SECRET HTML','SCHEMA')])
def test_permissions_and_bad_json_stop_without_retry(tmp_path,status,raw,want):
    clock=Clock(); session=Session(response(status,raw=raw))
    with pytest.raises(RuntimeError):
        pacing(tmp_path/'first',clock,tmp_path/'shared.json').call(TushareClient.from_token('SECRET',session=session).daily,{})
    assert len(session.calls)==1
    state=json.loads((tmp_path/'shared.json').read_text())
    assert state['failure']==want and state['http_status']==status
    assert 'SECRET' not in json.dumps(state)


@pytest.mark.parametrize('declared,want_complete',[(True,True),(False,False)])
def test_b1_keeps_declared_empty_and_undeclared_empty_distinct(tmp_path,declared,want_complete):
    req=request('daily',['000001.SZ'],'2026-09-30','2026-09-30')
    session=Session(response(payload={'code':0,'data':{'fields':req['fields'] if declared else [],'items':[]}}))
    clock=Clock(); result=collect_requests(TushareClient.from_token('SECRET',session=session),[req],tmp_path/'acq',tmp_path/'shared.json',.5,clock,clock.sleep)
    assert result['complete'] is want_complete
    assert (tmp_path/'acq/complete.json').exists() is want_complete
    if not declared: assert result['status']=='SCHEMA'


def test_legacy_error_clears_previous_typed_http_status(tmp_path):
    clock=Clock(); first=tmp_path/'first'; first.mkdir()
    (first/'acquisition.json').write_text(json.dumps({'http_status':503}),encoding='utf-8')
    def old_sdk(**params): raise RuntimeError('token SECRET unauthorized')
    with pytest.raises(RuntimeError): pacing(first,clock,tmp_path/'shared.json').call(old_sdk,{})
    state=json.loads((first/'acquisition.json').read_text())
    assert state['failure']=='PERMISSION' and 'http_status' not in state
