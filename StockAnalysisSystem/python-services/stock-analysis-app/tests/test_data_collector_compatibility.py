import sys
from unittest.mock import MagicMock

import pandas as pd


def test_data_collector_keeps_fetch_single_signature():
    from data_loader.collector import DataCollector

    repository = MagicMock()
    repository.fetch_single.return_value = pd.DataFrame({'close': [10.2]})
    collector = DataCollector(use_tushare=True, repository=repository)

    result = collector.fetch_single('000001.SZ', '20260801', '20260814')

    repository.fetch_single.assert_called_once_with(
        '000001.SZ', '20260801', '20260814'
    )
    assert result is repository.fetch_single.return_value


def test_data_collector_does_not_import_external_sdk(monkeypatch):
    from data_loader.collector import DataCollector

    monkeypatch.setitem(sys.modules, 'tushare', None)
    monkeypatch.setitem(sys.modules, 'akshare', None)
    repository = MagicMock()
    repository.fetch_stock_list.return_value = pd.DataFrame({
        'ts_code': ['000001.SZ']
    })

    result = DataCollector(repository=repository).fetch_stock_list()

    assert result.iloc[0]['ts_code'] == '000001.SZ'
    repository.fetch_stock_list.assert_called_once_with()


def test_collect_daily_data_preserves_old_dictionary_shape():
    from data_loader.collector import DataCollector

    repository = MagicMock()
    repository.load_daily_panel.return_value = pd.DataFrame({
        'ts_code': ['000001.SZ', '000001.SZ', '600000.SH'],
        'trade_date': pd.to_datetime(['2026-08-13', '2026-08-14', '2026-08-14']),
        'close': [10.0, 10.2, 12.5],
    })

    result = DataCollector(repository=repository).collect_daily_data(
        ['000001.SZ', '600000.SH'], '20260801', '20260814', delay=99
    )

    repository.load_daily_panel.assert_called_once_with(
        ['000001.SZ', '600000.SH'], '20260801', '20260814'
    )
    assert list(result) == ['000001.SZ', '600000.SH']
    assert len(result['000001.SZ']) == 2


def test_collect_all_stocks_uses_database_stock_list():
    from data_loader.collector import DataCollector

    repository = MagicMock()
    repository.fetch_stock_list.return_value = pd.DataFrame({
        'ts_code': ['000001.SZ', '600000.SH']
    })
    repository.load_daily_panel.return_value = pd.DataFrame({
        'ts_code': ['000001.SZ'],
        'trade_date': pd.to_datetime(['2026-08-14']),
        'close': [10.2],
    })

    result = DataCollector(repository=repository).collect_all_stocks(
        start_date='20260801', end_date='20260814'
    )

    repository.load_daily_panel.assert_called_once_with(
        ['000001.SZ', '600000.SH'], '20260801', '20260814'
    )
    assert list(result) == ['000001.SZ']
