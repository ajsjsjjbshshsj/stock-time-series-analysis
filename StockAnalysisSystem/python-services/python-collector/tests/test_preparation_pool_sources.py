"""Only complete single snapshots are frozen; industry dates must be truthful."""
import importlib
from datetime import datetime
from types import SimpleNamespace

import pandas as pd
import pytest


def cls():
    try:
        return importlib.import_module('app.preparation.pool_sources').PoolSources
    except ModuleNotFoundError:
        pytest.fail('pool source adapter not implemented')


def frame(size, index='000852.SH', day='20260930'):
    return pd.DataFrame([dict(index_code=index, con_code=f'{i:06d}.SZ', trade_date=day,
                              weight=100.0/size) for i in range(1, size+1)])


class Pacing:
    def call(self, method, params):
        return method(**params)


def sources(index_response=None, industry_response=None):
    def index_weight(**params):
        if index_response is None:
            return None
        # A real monthly endpoint must respect the date bounds sent by the adapter.
        days = index_response['trade_date']
        return index_response.loc[(days >= params['start_date']) & (days <= params['end_date'])].copy()
    return cls()(Pacing(), SimpleNamespace(index_weight=index_weight),
                 SimpleNamespace(industry_constituents=lambda code: industry_response),
                 clock=lambda: datetime.fromisoformat('2026-10-11T17:00:00+08:00'))


def test_csi1000_single_snapshot_is_complete():
    pool = sources(frame(1000)).acquire('index', '000852.SH', '2026-10-11')
    assert pool['stock_count'] == 1000
    assert pool['provider_snapshot_date'] == '2026-09-30'


def test_latest_snapshot_not_historical_union():
    data = pd.concat([frame(300, '000300.SH', '20260930'), frame(1, '000300.SH', '20260901')])
    pool = sources(data).acquire('index', '000300.SH', '2026-10-11')
    assert pool['stock_count'] == 300
    assert pool['provider_snapshot_date'] == '2026-09-30'


@pytest.mark.parametrize('kind', ['empty', 'short', 'duplicate', 'wrong_index', 'future', 'nan'])
def test_bad_index_response_never_becomes_frozen_pool(kind):
    data = frame(300, '000300.SH')
    if kind == 'empty': data = data.iloc[:0]
    elif kind == 'short': data = data.iloc[:-1]
    elif kind == 'duplicate': data = pd.concat([data, data.iloc[:1]])
    elif kind == 'wrong_index': data['index_code'] = '000852.SH'
    elif kind == 'future': data['trade_date'] = '20261012'
    elif kind == 'nan': data.loc[0, 'weight'] = float('nan')
    with pytest.raises(ValueError):
        sources(data).acquire('index', '000300.SH', '2026-10-11')


def test_industry_current_snapshot_has_no_fake_historical_provider_date():
    pool = sources(industry_response=pd.DataFrame({'代码':['600000', '000001']})).acquire(
        'industry', '银行', '2023-01-01')
    assert pool['provider'] == 'akshare.eastmoney'
    assert pool['provider_snapshot_date'] is None
    assert pool['acquired_at'] == '2026-10-11T17:00:00+08:00'
    assert pool['codes'] == ['000001.SZ', '600000.SH']


def test_unknown_index_refused_before_request():
    with pytest.raises(ValueError):
        sources(frame(300)).acquire('index', 'USER_TEXT', '2026-10-11')


def test_industry_920_codes_resolve_to_beijing_not_shanghai():
    pool=sources(industry_response=pd.DataFrame({'代码':['920118']})).acquire('industry','化学制品','2026-10-11')
    assert pool['codes']==['920118.BJ']
