from unittest.mock import Mock

from app.market_data.akshare_client import AkshareClient
from app.market_data.tushare_client import TushareClient


def test_tushare_client_delegates_daily_basic():
    pro = Mock()
    client = TushareClient(pro)

    client.daily_basic(trade_date="20260814")

    pro.daily_basic.assert_called_once_with(trade_date="20260814")


def test_tushare_client_delegates_all_supported_endpoints():
    pro = Mock()
    client = TushareClient(pro)

    client.daily(ts_code="000001.SZ")
    client.stock_basic(exchange="", list_status="L")
    client.trade_calendar(exchange="SSE")
    client.index_weight(index_code="000300.SH")

    pro.daily.assert_called_once_with(ts_code="000001.SZ")
    pro.stock_basic.assert_called_once_with(exchange="", list_status="L")
    pro.trade_cal.assert_called_once_with(exchange="SSE")
    pro.index_weight.assert_called_once_with(index_code="000300.SH")


def test_akshare_client_delegates_stock_history():
    sdk = Mock()
    client = AkshareClient(sdk)

    client.stock_history("000001", "20260801", "20260814")

    sdk.stock_zh_a_hist.assert_called_once_with(
        symbol="000001",
        period="daily",
        start_date="20260801",
        end_date="20260814",
        adjust="qfq",
    )


def test_akshare_client_delegates_reference_endpoints():
    sdk = Mock()
    client = AkshareClient(sdk)

    client.stock_info()
    client.industry_constituents("银行")
    client.index_constituents("000300")

    sdk.stock_info_a_code_name.assert_called_once_with()
    sdk.stock_board_industry_cons_em.assert_called_once_with(symbol="银行")
    sdk.index_stock_cons.assert_called_once_with(symbol="000300")
