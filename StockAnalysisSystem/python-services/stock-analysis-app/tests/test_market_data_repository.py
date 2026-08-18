from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd


MARKET_COLUMNS = [
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
    'pre_close', 'change', 'pct_chg', 'vol', 'amount',
    'turnover_rate', 'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
]


def _market_frame():
    return pd.DataFrame([{
        'ts_code': '000001.SZ',
        'trade_date': date(2026, 8, 14),
        'open': 10.0,
        'high': 10.5,
        'low': 9.8,
        'close': 10.2,
        'pre_close': 9.9,
        'change': 0.3,
        'pct_chg': 3.03,
        'vol': 100000,
        'amount': 102000,
        'turnover_rate': None,
        'pe': None,
        'pe_ttm': None,
        'pb': None,
        'ps': None,
        'total_mv': None,
    }])


def test_fetch_single_returns_old_market_columns_and_datetime():
    from data_loader.market_data_repository import MarketDataRepository

    session = MagicMock()
    with patch(
        'database.repository.pd.read_sql', return_value=_market_frame()
    ) as read_sql:
        result = MarketDataRepository(session).fetch_single(
            '000001.SZ', '20260801', '20260814'
        )

    assert list(result.columns) == MARKET_COLUMNS
    assert pd.api.types.is_datetime64_any_dtype(result['trade_date'])
    query = str(read_sql.call_args.args[0])
    assert 'LEFT JOIN `stock_daily_basic`' in query
    assert 'sd.ts_code = sdb.ts_code' in query
    assert 'sd.trade_date = sdb.trade_date' in query
    assert read_sql.call_args.kwargs['params'] == {
        'code_0': '000001.SZ',
        'start_date': date(2026, 8, 1),
        'end_date': date(2026, 8, 14),
    }


def test_missing_daily_basic_preserves_ohlcv_row():
    from data_loader.market_data_repository import MarketDataRepository

    with patch(
        'database.repository.pd.read_sql', return_value=_market_frame()
    ):
        result = MarketDataRepository(MagicMock()).fetch_single(
            '000001.SZ', '20260801', '20260814'
        )

    assert len(result) == 1
    assert result.iloc[0]['close'] == 10.2
    assert pd.isna(result.iloc[0]['turnover_rate'])


def test_load_daily_panel_filters_multiple_codes():
    from data_loader.market_data_repository import MarketDataRepository

    with patch(
        'database.repository.pd.read_sql', return_value=_market_frame()
    ) as read_sql:
        MarketDataRepository(MagicMock()).load_daily_panel(
            ['000001.SZ', '600000.SH'], '20260801', '20260814'
        )

    params = read_sql.call_args.kwargs['params']
    assert params['code_0'] == '000001.SZ'
    assert params['code_1'] == '600000.SH'


def test_fetch_single_keeps_plain_six_digit_code_compatibility():
    from data_loader.market_data_repository import MarketDataRepository

    with patch(
        'database.repository.pd.read_sql', return_value=_market_frame()
    ) as read_sql:
        MarketDataRepository(MagicMock()).fetch_single(
            '000001', '20260801', '20260814'
        )

    assert read_sql.call_args.kwargs['params']['code_0'] == '000001.SZ'


def test_latest_constituent_snapshot_is_selected_when_date_omitted():
    from data_loader.market_data_repository import MarketDataRepository

    frame = pd.DataFrame([{
        'group_type': 'index',
        'group_code': '000300.SH',
        'ts_code': '000001.SZ',
        'as_of_date': date(2026, 8, 14),
        'weight': 0.5,
        'source': 'tushare',
    }])
    with patch('database.repository.pd.read_sql', return_value=frame) as read_sql:
        result = MarketDataRepository(MagicMock()).get_index_constituents(
            '000300.SH'
        )

    query = str(read_sql.call_args.args[0])
    assert 'MAX(as_of_date)' in query
    assert read_sql.call_args.kwargs['params'] == {
        'group_type': 'index',
        'group_code': '000300.SH',
    }
    assert pd.api.types.is_datetime64_any_dtype(result['as_of_date'])


def test_index_constituents_keep_plain_index_code_compatibility():
    from data_loader.market_data_repository import MarketDataRepository

    with patch(
        'database.repository.pd.read_sql', return_value=pd.DataFrame()
    ) as read_sql:
        MarketDataRepository(MagicMock()).get_index_constituents('000300')

    assert read_sql.call_args.kwargs['params']['group_code'] == '000300.SH'


def test_explicit_constituent_snapshot_uses_requested_date():
    from data_loader.market_data_repository import MarketDataRepository

    with patch(
        'database.repository.pd.read_sql', return_value=pd.DataFrame()
    ) as read_sql:
        MarketDataRepository(MagicMock()).get_industry_constituents(
            '银行', '20260814'
        )

    assert read_sql.call_args.kwargs['params'] == {
        'group_type': 'industry',
        'group_code': '银行',
        'as_of_date': date(2026, 8, 14),
    }
