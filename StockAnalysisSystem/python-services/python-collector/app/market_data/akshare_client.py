"""Thin, configuration-free wrapper around the AkShare module."""


class AkshareClient:
    def __init__(self, sdk):
        self.sdk = sdk

    def stock_history(self, symbol: str, start_date: str, end_date: str):
        return self.sdk.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

    def stock_info(self):
        return self.sdk.stock_info_a_code_name()

    def industry_constituents(self, industry_code: str):
        return self.sdk.stock_board_industry_cons_em(symbol=industry_code)

    def index_constituents(self, index_code: str):
        return self.sdk.index_stock_cons(symbol=index_code)
