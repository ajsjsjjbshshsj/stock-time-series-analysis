from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from app.collectors.akshare_collector import AkShareCollector


def test_collect_daily_single_maps_turnover_rate_and_beijing_code():
    client = MagicMock()
    client.stock_history.return_value = pd.DataFrame({
        '日期': ['2026-08-14'],
        '开盘': [10.0],
        '收盘': [10.2],
        '最高': [10.5],
        '最低': [9.8],
        '成交量': [1000],
        '成交额': [10200],
        '涨跌额': [0.2],
        '涨跌幅': [2.0],
        '换手率': [1.25],
    })
    collector = AkShareCollector(client=client)

    result = collector.collect_daily_single('830799', '20260814', '20260814')

    assert result.iloc[0]['ts_code'] == '830799.BJ'
    assert result.iloc[0]['turnover_rate'] == 1.25
    assert isinstance(result.iloc[0]['trade_date'], date)


def test_collect_daily_basic_returns_null_valuations_for_akshare():
    client = MagicMock()
    client.stock_history.return_value = pd.DataFrame({
        '日期': ['2026-08-14'],
        '开盘': [10.0],
        '收盘': [10.2],
        '最高': [10.5],
        '最低': [9.8],
        '成交量': [1000],
        '成交额': [10200],
        '涨跌额': [0.2],
        '涨跌幅': [2.0],
        '换手率': [1.25],
    })
    collector = AkShareCollector(client=client)

    result = collector.collect_daily_basic(
        '20260814', ts_code='000001.SZ'
    )

    assert result.iloc[0]['turnover_rate'] == 1.25
    assert result.iloc[0]['pe'] is None
    assert result.iloc[0]['total_mv'] is None


def test_collect_industry_constituents_normalizes_codes():
    client = MagicMock()
    client.industry_constituents.return_value = pd.DataFrame({
        '代码': ['000001', '830799'],
        '名称': ['平安银行', '艾融软件'],
    })
    collector = AkShareCollector(client=client)

    result = collector.collect_industry_constituents('银行', '20260814')

    assert list(result['ts_code']) == ['000001.SZ', '830799.BJ']
    assert (result['group_type'] == 'industry').all()
