"""分析应用访问原始市场数据的唯一数据库边界。"""

import pandas as pd

from database import repository


MARKET_DATA_COLUMNS = [
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
    'pre_close', 'change', 'pct_chg', 'vol', 'amount',
    'turnover_rate', 'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
]


class MarketDataRepository:
    """为分析层提供稳定、与旧行情接口兼容的 DataFrame。"""

    def __init__(self, session):
        self.session = session

    def fetch_single(self, code, start_date, end_date):
        frame = repository.load_daily_panel(
            self.session, [_to_db_code(code)], start_date, end_date
        )
        return self._normalize_market_frame(frame)

    def fetch_stock_list(self):
        return repository.load_stock_basic_df(self.session)

    def load_daily_panel(self, stock_codes, start_date=None, end_date=None):
        normalized_codes = (
            [_to_db_code(code) for code in stock_codes]
            if stock_codes is not None else None
        )
        frame = repository.load_daily_panel(
            self.session, normalized_codes, start_date, end_date
        )
        return self._normalize_market_frame(frame)

    def get_index_constituents(self, index_code, as_of_date=None):
        return self._load_constituents(
            'index', _to_index_code(index_code), as_of_date
        )

    def get_industry_constituents(self, industry_code, as_of_date=None):
        return self._load_constituents('industry', industry_code, as_of_date)

    def _load_constituents(self, group_type, group_code, as_of_date):
        frame = repository.load_constituents(
            self.session, group_type, group_code, as_of_date
        )
        if 'as_of_date' in frame.columns:
            frame = frame.copy()
            frame['as_of_date'] = pd.to_datetime(frame['as_of_date'])
        return frame

    @staticmethod
    def _normalize_market_frame(frame):
        normalized = frame.copy()
        for column in MARKET_DATA_COLUMNS:
            if column not in normalized.columns:
                normalized[column] = None
        normalized = normalized.loc[:, MARKET_DATA_COLUMNS]
        normalized['trade_date'] = pd.to_datetime(normalized['trade_date'])
        return normalized


def _to_db_code(code):
    """将旧接口接受的纯数字代码转换为数据库统一代码。"""
    normalized = str(code).strip().upper()
    if '.' in normalized:
        return normalized
    if normalized.startswith(('4', '8')):
        return f'{normalized}.BJ'
    if normalized.startswith(('6', '9')):
        return f'{normalized}.SH'
    return f'{normalized}.SZ'


def _to_index_code(code):
    """兼容旧命令中的纯 6 位指数代码。"""
    normalized = str(code).strip().upper()
    if '.' in normalized:
        return normalized
    suffix = 'SZ' if normalized.startswith('399') else 'SH'
    return f'{normalized}.{suffix}'
