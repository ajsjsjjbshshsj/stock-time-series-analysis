import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pandas as pd
import pytest
from nightly_support import reference
from app.nightly.contracts import FIELDS

NOW=1791709200.0
class Pacing:
    def call(self,method,params):return method(**params)

def module():return importlib.import_module('app.nightly.universe')

def client(rows,calls):
    class Client:
        def stock_basic(self,**params):
            calls.append(params)
            assert params['fields']==','.join(FIELDS)
            selected=[r for r in rows if r['exchange']==params['exchange'] and r['list_status']==params['list_status']]
            return pd.DataFrame(selected,columns=FIELDS)
    return Client()

def test_six_reference_partitions_are_sealed_and_freeze_target_market(tmp_path):
    calls=[];rows=[reference(),reference('600000.SH',exchange='SSE'),
        reference('000002.SZ',list_status='D',delist_date='20260929'),reference('000003.SZ',list_status='P')]
    provider=module().MarketUniverseProvider(lambda:client(rows,calls),lambda directory:Pacing(),clock=lambda:NOW)
    result=provider.freeze('2026-09-28',tmp_path)
    assert result['codes']==['000001.SZ','000002.SZ','000003.SZ','600000.SH']
    assert {(c['exchange'],c['list_status']) for c in calls}=={(e,s) for e in ('SSE','SZSE') for s in ('L','D','P')}
    assert len(calls)==6
    assert len(list(tmp_path.rglob('*.parquet')))==6

def test_verified_reference_recovers_without_sdk_and_detects_tamper(tmp_path):
    calls=[];provider=module().MarketUniverseProvider(lambda:client([reference(),reference('600000.SH',exchange='SSE')],calls),lambda directory:Pacing(),clock=lambda:NOW)
    first=provider.freeze('2026-09-28',tmp_path)
    def forbidden():raise AssertionError('SDK_ON_VERIFIED_REFERENCE')
    recovered=module().MarketUniverseProvider(forbidden,lambda directory:Pacing(),clock=lambda:NOW+1)
    assert recovered.freeze('2026-09-28',tmp_path)==first
    raw=next(tmp_path.rglob('*.parquet'));raw.write_bytes(b'bad')
    with pytest.raises(ValueError):recovered.freeze('2026-09-28',tmp_path)

def test_historical_batch_can_pin_one_verified_reference_and_derive_dates(tmp_path):
    calls=[];provider=module().MarketUniverseProvider(lambda:client([reference(),reference('301001.SZ',market='创业板',list_date='20260929'),reference('688001.SH',exchange='SSE',market='科创板',list_date='20260930')],calls),lambda directory:Pacing(),clock=lambda:NOW)
    bundle=provider.acquire_reference(tmp_path/'reference')
    def forbidden():raise AssertionError('REPEATED_BATCH_REFERENCE')
    pinned=module().MarketUniverseProvider(forbidden,lambda directory:Pacing(),clock=lambda:NOW,pinned_reference=bundle)
    first=pinned.freeze('2026-09-28',tmp_path/'day1')
    second=pinned.freeze('2026-09-29',tmp_path/'day2')
    assert first['codes']==['000001.SZ'] and second['codes']==['000001.SZ','301001.SZ']
    assert first['source_manifest_sha256']==second['source_manifest_sha256']
    assert len(calls)==6

@pytest.mark.parametrize('bad',['schema','exchange','status','capacity','empty'])
def test_bad_or_incomplete_reference_partitions_are_not_frozen(tmp_path,bad):
    class Client:
        def stock_basic(self,**params):
            rows=[reference()]
            if bad=='exchange':rows=[reference('920001.BJ',exchange='BSE',market='北交所')]
            if bad=='status':rows=[reference(list_status='UNKNOWN')]
            if bad=='capacity':rows=[reference()]*6000
            if bad=='empty':rows=[]
            frame=pd.DataFrame(rows,columns=FIELDS)
            if bad=='schema':frame=frame.drop(columns='exchange')
            return frame
    provider=module().MarketUniverseProvider(lambda:Client(),lambda directory:Pacing(),clock=lambda:NOW)
    with pytest.raises(ValueError):provider.freeze('2026-09-28',tmp_path)

def test_reference_future_day_rejected_before_sdk(tmp_path):
    def forbidden():raise AssertionError('SDK_FOR_FUTURE_DAY')
    with pytest.raises(ValueError):module().MarketUniverseProvider(forbidden,lambda directory:Pacing(),clock=lambda:NOW).freeze('2027-01-01',tmp_path)

def legacy_port():
    # Actual provider identity retained verbatim, not mapped to 600018.SH.
    return reference('T600018.SH',symbol='T600018',name='上港集箱(退)',exchange='SSE',market=None,
        area=None,industry=None,list_status='D',list_date='20000719',delist_date='20061020')

def test_verified_pre_scope_legacy_alias_remains_in_raw_audit_not_target_members(tmp_path):
    calls=[];rows=[reference(),reference('600018.SH',exchange='SSE',list_date='20061026'),legacy_port()]
    provider=module().MarketUniverseProvider(lambda:client(rows,calls),lambda directory:Pacing(),clock=lambda:NOW)
    snapshot=provider.freeze('2026-09-28',tmp_path)
    assert snapshot['codes']==['000001.SZ','600018.SH']
    raw=pd.read_parquet(tmp_path/'SSE_D.parquet')
    assert raw['ts_code'].tolist()==['T600018.SH'] and raw['symbol'].tolist()==['T600018']
    bundle=module().verify_reference(tmp_path)
    assert bundle['excluded_references']==[dict(ts_code='T600018.SH',reason='VERIFIED_LEGACY_RETIRED_BEFORE_SCOPE',delist_date='2006-10-20')]
    assert next(row for row in bundle['rows'] if row['ts_code']=='T600018.SH')==legacy_port()
    def forbidden():raise AssertionError('SDK_ON_RETAINED_LEGACY_REFERENCE')
    assert module().MarketUniverseProvider(forbidden,lambda directory:Pacing(),clock=lambda:NOW).freeze('2026-09-28',tmp_path)==snapshot

@pytest.mark.parametrize('change',[dict(delist_date='20250928'),dict(list_status='L'),dict(symbol='600018'),dict(exchange='SZSE')])
def test_legacy_alias_exception_never_excludes_in_scope_or_mismatched_identity(tmp_path,change):
    calls=[];old=legacy_port();old.update(change)
    rows=[reference(),reference('600000.SH',exchange='SSE'),old]
    provider=module().MarketUniverseProvider(lambda:client(rows,calls),lambda directory:Pacing(),clock=lambda:NOW)
    with pytest.raises(ValueError):provider.freeze('2026-09-28',tmp_path)
