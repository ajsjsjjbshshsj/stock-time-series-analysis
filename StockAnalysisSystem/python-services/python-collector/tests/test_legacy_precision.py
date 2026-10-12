"""Legacy float32 compatibility is explicit, reproducible and non-mutating."""
from copy import deepcopy
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from preparation_market_support import fixture
from app.market_data.repair_snapshot import classify_sources
from app.market_data.repair_contract import DAILY
from app.preparation.stock_inventory import build_stock_inventory


def inputs():
    pool,window,tables,evidence=fixture()
    tables['schema']['stock_daily']['types'].update(vol='decimal(24,4)',amount='decimal(24,4)')
    raw=dict(ts_code='000001.SZ',trade_date='20260928',open=58.08,high=59.,low=58.,close=58.5,
             pre_close=58.,change=.5,pct_chg=.8621,vol=494648.83,amount=90550.232)
    old=tables['stock_daily'][0]
    old.update({key:value for key,value in raw.items() if key not in ('ts_code','trade_date')})
    old.update(open='58.080002',vol='494648.8438',amount='90550.2344')
    source=dict(daily=[raw],daily_basic=[],adj_factor=evidence['adj_factor'],suspend_d=[])
    return dict(pool=pool,calendar=evidence['calendar'],tables=tables),source


def test_frozen_default_remains_strict_and_has_no_new_audit_fields():
    data,source=inputs();result=classify_sources(data,source)
    assert result['report']['source_issue_counts']['EXISTING_KEY_CONFLICT']==1
    assert 'legacy_float32_compatible' not in result


def test_exact_legacy_pipeline_is_compatible_without_replacing_existing_values():
    data,source=inputs();saved=deepcopy((data,source))
    result=classify_sources(data,source,allow_verified_legacy_float32=True)
    assert result['report']['source_issue_counts']['EXISTING_KEY_CONFLICT']==0
    assert result['candidates']['stock_daily']==[]
    old=result['daily'][0]
    assert old['open']=='58.080002' and old['vol']=='494648.8438' and old['amount']=='90550.2344'
    assert (data,source)==saved
    audit=result['legacy_float32_compatible']
    assert len(audit)==1 and audit[0]['ts_code']=='000001.SZ'
    assert audit[0]['changed_fields']==['amount','open','vol']
    assert result['report']['legacy_float32_compatible_count']==1


@pytest.mark.parametrize('field,value',[('close',58.6),('amount',90551.),('vol',494650.)])
def test_genuine_differences_remain_conflicts(field,value):
    data,source=inputs();source['daily'][0][field]=value
    result=classify_sources(data,source,allow_verified_legacy_float32=True)
    assert result['report']['source_issue_counts']['EXISTING_KEY_CONFLICT']==1
    assert result['legacy_float32_compatible']==[]


def test_missing_schema_does_not_enable_arbitrary_tolerance():
    data,source=inputs();data['tables']['schema']['stock_daily']['types'].pop('vol')
    result=classify_sources(data,source,allow_verified_legacy_float32=True)
    assert result['report']['source_issue_counts']['EXISTING_KEY_CONFLICT']==1

@pytest.mark.parametrize('stored,accepted',[
    ('99999998430674944.0000',True),('99999998430674940.0000',False),
])
def test_binary32_projection_preserves_exact_integer_digits(stored,accepted):
    data,source=inputs();source['daily'][0]['amount']=1e17
    data['tables']['stock_daily'][0]['amount']=stored
    result=classify_sources(data,source,allow_verified_legacy_float32=True)
    assert (result['report']['source_issue_counts']['EXISTING_KEY_CONFLICT']==0) is accepted


@pytest.mark.parametrize('value',[1,'yes',None])
def test_policy_requires_explicit_boolean(value):
    data,source=inputs()
    with pytest.raises(ValueError):classify_sources(data,source,allow_verified_legacy_float32=value)

def test_inventory_only_enables_precision_compatibility_when_explicitly_requested():
    data,source=inputs()
    evidence=dict(source,calendar=data['calendar'],queried_suspensions=[])
    window=dict(start=data['tables']['start'],end=data['tables']['end'],empty=False)
    old=build_stock_inventory(data['pool'],window,data['tables'],evidence)
    assert not old['data_ready']
    new=build_stock_inventory(data['pool'],window,data['tables'],evidence,allow_verified_legacy_float32=True)
    assert new['data_ready']
    assert len(new['attribution']['legacy_float32_compatible'])==1
