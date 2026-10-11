"""Thin, configuration-free wrapper around a Tushare Pro client."""


class TushareClient:
    def __init__(self, pro):
        self.pro = pro

    @classmethod
    def from_token(cls, token, timeout=20, session=None):
        """Opt-in checked transport; legacy injected SDK clients remain supported."""
        from app.market_data.checked_tushare_api import CheckedTushareApi
        return cls(CheckedTushareApi(token,timeout=timeout,session=session))

    def daily(self, **kwargs):
        return self.pro.daily(**kwargs)

    def daily_basic(self, **kwargs):
        return self.pro.daily_basic(**kwargs)

    def adj_factor(self, **kwargs):
        return self.pro.adj_factor(**kwargs)

    def suspend_d(self, **kwargs):
        return self.pro.suspend_d(**kwargs)

    def stock_basic(self, **kwargs):
        return self.pro.stock_basic(**kwargs)

    def trade_calendar(self, **kwargs):
        return self.pro.trade_cal(**kwargs)

    def index_weight(self, **kwargs):
        return self.pro.index_weight(**kwargs)
