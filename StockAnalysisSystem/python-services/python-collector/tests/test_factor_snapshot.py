"""Real collector behavior, with a deterministic fake only at remote API edge."""
import pandas as pd
import pytest


def market():
    return pd.DataFrame({'ts_code':['000001.SZ']*2,'trade_date':pd.to_datetime(['2024-01-02','2024-01-03'])})


class Api:
    def __init__(self,fail=None):
        self.fail=fail
        self.calls=[]
    def adj_factor(self,**kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError(self.fail)
        return pd.DataFrame({'ts_code':['000001.SZ']*2,'trade_date':['20240102','20240103'],'adj_factor':[1.,1.]})


def test_factors_validate_and_resume_without_request(tmp_path):
    from app.market_data.factor_snapshot import collect_factor_snapshot
    api=Api()
    first=collect_factor_snapshot(api,market(),tmp_path,interval=.5)
    assert first.adj_factor.tolist()==[1.,1.]
    assert api.calls==[dict(ts_code='000001.SZ',start_date='20240102',end_date='20240103',fields='ts_code,trade_date,adj_factor')]
    second=collect_factor_snapshot(Api(fail='should not request'),market(),tmp_path,interval=.5)
    pd.testing.assert_frame_equal(first,second)


def test_permissions_stop_without_retry(tmp_path):
    from app.market_data.factor_snapshot import collect_factor_snapshot
    api=Api('没有访问权限')
    with pytest.raises(RuntimeError,match='权限'):
        collect_factor_snapshot(api,market(),tmp_path,interval=.5)
    assert len(api.calls)==1


def test_hourly_quota_preserves_failure_and_stops(tmp_path):
    import json
    from app.market_data.factor_snapshot import collect_factor_snapshot
    api=Api('频率超限(1次/小时)')
    with pytest.raises(RuntimeError,match='小时'):
        collect_factor_snapshot(api,market(),tmp_path,interval=.5)
    assert len(api.calls)==1
    ledger=json.loads((tmp_path/'acquisition.json').read_text(encoding='utf-8'))
    assert ledger['retry_not_before']>0 and ledger['failed']['ts_code']=='000001.SZ'


def test_partial_or_wrong_stock_response_is_not_cached(tmp_path):
    from app.market_data.factor_snapshot import collect_factor_snapshot
    class Partial(Api):
        def adj_factor(self,**kwargs):
            return super().adj_factor(**kwargs).iloc[:1]
    with pytest.raises(ValueError,match='coverage'):
        collect_factor_snapshot(Partial(),market(),tmp_path,interval=.5)
    assert not list(tmp_path.glob('*.parquet'))


def test_pacing_applies_to_every_actual_request(tmp_path):
    from app.market_data.factor_snapshot import collect_factor_snapshot
    now=[0.]
    events=[]
    def sleep(seconds):
        now[0]+=seconds
    class Multi(Api):
        def adj_factor(self,**kwargs):
            events.append(now[0])
            return super().adj_factor(**kwargs).assign(ts_code=kwargs['ts_code'])
    panel=pd.concat([market(),market().assign(ts_code='000002.SZ')])
    collect_factor_snapshot(Multi(),panel,tmp_path,interval=.5,sleep=sleep,clock=lambda:now[0])
    assert len(events)==2 and events[1]-events[0]>=.5


def test_hourly_only_message_blocks_subsequent_resume(tmp_path):
    import json
    from app.market_data.factor_snapshot import collect_factor_snapshot
    api=Api('抱歉，您每小时最多访问该接口1次')
    with pytest.raises(RuntimeError,match='小时'):
        collect_factor_snapshot(api,market(),tmp_path,clock=lambda:100.)
    ledger=json.loads((tmp_path/'acquisition.json').read_text(encoding='utf-8'))
    assert ledger['retry_not_before']>=3701.
    resumed=Api()
    with pytest.raises(RuntimeError,match='cooldown'):
        collect_factor_snapshot(resumed,market(),tmp_path,clock=lambda:101.)
    assert resumed.calls==[]
