"""Real protocol/client behavior; fake only the external HTTP transport."""
import importlib
import json
import traceback

import pandas as pd
import pytest
import requests


def api():
    try: return importlib.import_module('app.market_data.checked_tushare_api')
    except ModuleNotFoundError: pytest.fail('Checked Tushare HTTP boundary not implemented')


def response(status=200,payload=None,raw=None):
    result=requests.Response(); result.status_code=status
    result._content=(raw if raw is not None else json.dumps(payload if payload is not None else
        {'code':0,'msg':'','data':{'fields':['ts_code','pe'],'items':[['000001.SZ',None]]}})).encode('utf-8')
    result.headers['Content-Type']='application/json'
    return result


class Session:
    def __init__(self,*items): self.items=list(items); self.calls=[]
    def post(self,url,**kwargs):
        self.calls.append(dict(url=url,**kwargs))
        item=self.items.pop(0)
        if isinstance(item,Exception): raise item
        return item


@pytest.mark.parametrize('status,kind',[(408,'NETWORK'),(500,'NETWORK'),(503,'NETWORK'),
    (429,'MINUTE_QUOTA'),(401,'PERMISSION'),(403,'PERMISSION'),(400,'PERMISSION'),(302,'PROVIDER')])
def test_http_errors_never_become_empty_data(status,kind):
    m=api(); session=Session(response(status,raw='SECRET bad HTTP body'))
    client=m.CheckedTushareApi('SECRET',session=session)
    with pytest.raises(m.TushareRequestError) as caught:
        client.daily(ts_code='000001.SZ')
    assert caught.value.kind==kind and caught.value.http_status==status
    assert 'SECRET' not in ''.join(traceback.format_exception(caught.value))
    assert len(session.calls)==1  # no nested transport retry


@pytest.mark.parametrize('status,message,kind',[(429,'每小时最多访问 Token SECRET 权限详情','HOURLY_QUOTA'),
    (200,'每分钟最多访问，权限详情 SECRET','MINUTE_QUOTA'),(200,'每小时 Token SECRET 访问超限','HOURLY_QUOTA'),
    (200,'token SECRET 无效','PERMISSION'),(200,'参数 SECRET 错误','PERMISSION'),
    (200,'temporary connection timeout SECRET','NETWORK'),(200,'unsupported SECRET','PROVIDER')])
def test_provider_quota_is_not_swallowed_by_generic_permission_text(status,message,kind):
    m=api(); session=Session(response(status,{'code':-2001,'msg':message,'data':None}))
    with pytest.raises(m.TushareRequestError) as caught: m.CheckedTushareApi('SECRET',session=session).daily()
    assert caught.value.kind==kind
    assert 'SECRET' not in str(caught.value) and 'SECRET' not in repr(caught.value)


@pytest.mark.parametrize('payload',[[],{'code':True,'data':{}},{'code':0,'data':None},
    {'code':0,'data':{'fields':['pe','pe'],'items':[]}},
    {'code':0,'data':{'fields':['pe'],'items':[[1,2]]}},
    {'code':0,'data':{'fields':['pe'],'items':[{'pe':1}]}},
    {'code':0,'data':{'fields':None,'items':[]}}])
def test_malformed_business_package_is_rejected(payload):
    m=api()
    with pytest.raises(m.TushareRequestError) as caught: m.CheckedTushareApi('SECRET',session=Session(response(payload=payload))).daily()
    assert caught.value.kind=='SCHEMA'


def test_bad_json_is_safe_schema_error():
    m=api()
    client=m.CheckedTushareApi('SECRET',session=Session(response(raw='SECRET HTML')))
    with pytest.raises(m.TushareRequestError) as caught: client.daily()
    assert caught.value.kind=='SCHEMA' and 'SECRET' not in ''.join(traceback.format_exception(caught.value))


def test_wire_protocol_preserves_null_rows_and_does_not_follow_redirects():
    m=api(); session=Session(response(payload={'code':0,'msg':'','data':{
        'fields':['ts_code','pe'],'items':[['000001.SZ',None],['000002.SZ',10.]]}}))
    frame=m.CheckedTushareApi('SECRET',timeout=20,session=session).daily_basic(trade_date='20260930',fields='ts_code,pe')
    assert frame.ts_code.tolist()==['000001.SZ','000002.SZ'] and pd.isna(frame.pe.iloc[0]) and frame.pe.iloc[1]==10.
    assert session.calls==[dict(url='http://api.waditu.com/dataapi/daily_basic',timeout=20.,allow_redirects=False,
        json=dict(api_name='daily_basic',token='SECRET',params={'trade_date':'20260930'},fields='ts_code,pe'))]


@pytest.mark.parametrize('fields,want_shape',[(['ts_code','pe'],(0,2)),([],(0,0))])
def test_success_empty_result_does_not_invent_returned_fields(fields,want_shape):
    m=api(); frame=m.CheckedTushareApi('SECRET',session=Session(response(payload={
        'code':0,'data':{'fields':fields,'items':[]}}))).daily(fields='ts_code,pe')
    assert frame.shape==want_shape and list(frame.columns)==fields


@pytest.mark.parametrize('name',['../daily','https://evil/daily','_token','__dict__'])
def test_invalid_api_name_is_refused_before_transport(name):
    m=api(); session=Session(response())
    with pytest.raises(ValueError): m.CheckedTushareApi('SECRET',session=session).query(name)
    assert not session.calls


@pytest.mark.parametrize('timeout',[0,-1,float('inf'),float('nan'),True])
def test_nonfinite_or_invalid_timeout_is_refused(timeout):
    with pytest.raises(ValueError): api().CheckedTushareApi('SECRET',timeout=timeout,session=Session())


def test_factory_preserves_old_injection_and_uses_checked_protocol():
    from app.market_data.tushare_client import TushareClient
    assert callable(getattr(TushareClient,'from_token',None)),'Checked client factory not implemented'
    session=Session(response())
    assert len(TushareClient.from_token('SECRET',session=session).daily(ts_code='000001.SZ'))==1
    class Pro:
        def __getattr__(self,name): return lambda **params:(name,params)
    old=TushareClient(Pro())
    for method,remote in [('daily','daily'),('adj_factor','adj_factor'),('suspend_d','suspend_d'),
        ('trade_calendar','trade_cal'),('index_weight','index_weight')]:
        assert getattr(old,method)(trade_date='20260930')==(remote,{'trade_date':'20260930'})


@pytest.mark.parametrize('transport_error',[requests.ConnectionError('SECRET connection'),requests.exceptions.ChunkedEncodingError('SECRET incomplete read')])
def test_safe_transport_error_does_not_retain_original_exception(transport_error):
    m=api(); client=m.CheckedTushareApi('SECRET',session=Session(transport_error))
    with pytest.raises(m.TushareRequestError) as caught: client.daily()
    assert caught.value.kind=='NETWORK'
    assert caught.value.__context__ is None and caught.value.__cause__ is None


def test_bad_json_does_not_retain_response_document_through_context():
    m=api(); client=m.CheckedTushareApi('SECRET',session=Session(response(raw='SECRET body')))
    with pytest.raises(m.TushareRequestError) as caught: client.daily()
    assert caught.value.kind=='SCHEMA'
    assert caught.value.__context__ is None and caught.value.__cause__ is None
