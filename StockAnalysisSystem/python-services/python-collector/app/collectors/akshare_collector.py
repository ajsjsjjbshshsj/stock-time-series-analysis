"""
AkShare 数据采集器

职责：
    1. 调用 AkShare 接口
    2. 将 AkShare 字段转换为统一字段
    3. 返回与 TushareCollector 相同的数据格式
    4. 在必要时作为备用数据源

AkShare 特点：
    - 免费，无需 token
    - 返回中文列名
    - 不支持按交易日批量拉取全市场，需逐股拉取
    - 无 pre_close 字段，需从 close 和 pct_chg 反推
"""

from datetime import datetime

import akshare as ak
import pandas as pd

from app.collectors.base_collector import BaseCollector
from app.market_data.akshare_client import AkshareClient
from app.market_data.codes import to_ts_code
from app.utils.logger import get_logger

logger = get_logger(__name__)

# AkShare 中文列名 → 统一英文列名
_AKSHARE_RENAME = {
    '日期': 'trade_date',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'vol',
    '成交额': 'amount',
    '涨跌额': 'change',
    '涨跌幅': 'pct_chg',
    '换手率': 'turnover_rate',
}


_to_tushare_code = to_ts_code


def _normalize_akshare(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    标准化 AkShare 返回的 DataFrame。

    - 重命名列
    - 添加 ts_code 列
    - 反推 pre_close
    - 转换 trade_date 为 date 类型
    """
    df = df.copy()
    df = df.rename(columns=_AKSHARE_RENAME)

    # 添加 ts_code
    ts_code = to_ts_code(symbol)
    df['ts_code'] = ts_code

    # trade_date 转 date
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

    # 反推 pre_close = close / (1 + pct_chg / 100)
    if 'close' in df.columns and 'pct_chg' in df.columns:
        mask = df['pct_chg'].notna() & (df['pct_chg'] != 0)
        df.loc[mask, 'pre_close'] = (
            df.loc[mask, 'close'] / (1 + df.loc[mask, 'pct_chg'] / 100)
        ).round(2)

    # 确保标准列存在
    standard_cols = [
        'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
        'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        'turnover_rate',
    ]
    for col in standard_cols:
        if col not in df.columns:
            df[col] = None

    return df[standard_cols]


class AkShareCollector(BaseCollector):
    """AkShare 数据采集器"""

    def __init__(self, client: AkshareClient | None = None):
        self.client = client or AkshareClient(ak)
        logger.info("AkShareCollector 初始化完成")

    @property
    def source_name(self) -> str:
        return 'akshare'

    def collect_daily(self, trade_date: str) -> pd.DataFrame:
        """
        AkShare 不支持按交易日拉取全市场。
        此方法返回空 DataFrame，采集应使用 collect_daily_single 逐股进行。

        如需按日期采集全市场，应先获取股票列表，再逐股调用 collect_daily_single。
        """
        logger.warning(
            "AkShare 不支持按交易日批量拉取全市场数据，"
            "请使用 collect_daily_single 逐股采集"
        )
        return pd.DataFrame()

    def collect_daily_single(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        采集单只股票日线数据。

        Args:
            ts_code: 股票代码（Tushare 格式或 6 位数字）
            start_date: YYYYMMDD
            end_date: YYYYMMDD
        """
        symbol = str(ts_code).split('.')[0]
        start_date = start_date.replace('-', '')
        end_date = end_date.replace('-', '')

        df = self.client.stock_history(symbol, start_date, end_date)

        if df is None or df.empty:
            logger.info(f"AkShare {symbol} 日线数据: 无数据")
            return pd.DataFrame()

        df = _normalize_akshare(df, symbol)
        logger.info(f"AkShare {symbol} 日线数据: {len(df)} 条")
        return df

    def collect_basic(self) -> pd.DataFrame:
        """
        获取 A 股基本信息。

        返回统一格式的 DataFrame（ts_code, symbol, name）。
        """
        df = self.client.stock_info()
        if df is None or df.empty:
            logger.warning("AkShare 获取股票信息为空")
            return pd.DataFrame()

        # 标准化列名
        result = pd.DataFrame()
        if '代码' in df.columns:
            result['symbol'] = df['代码']
            result['ts_code'] = df['代码'].apply(to_ts_code)
        if '名称' in df.columns:
            result['name'] = df['名称']

        # 添加空列保持兼容
        for col in ['area', 'industry', 'list_date']:
            result[col] = None

        logger.info(f"AkShare 获取股票信息: {len(result)} 只")
        return result

    def collect_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        AkShare 无交易日历接口。
        返回空 DataFrame，由 date_utils 使用周末判断降级处理。
        """
        logger.warning("AkShare 无交易日历接口，将使用周末判断模式")
        return pd.DataFrame()

    def collect_daily_basic(
        self,
        trade_date: str,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        if not ts_code:
            logger.warning("AkShare daily_basic 需要指定 ts_code")
            return pd.DataFrame()
        daily = self.collect_daily_single(ts_code, trade_date, trade_date)
        if daily.empty:
            return pd.DataFrame()
        result = daily[['ts_code', 'trade_date', 'turnover_rate']].copy()
        for column in ['pe', 'pe_ttm', 'pb', 'ps', 'total_mv']:
            result[column] = None
        return result[
            ['ts_code', 'trade_date', 'turnover_rate',
             'pe', 'pe_ttm', 'pb', 'ps', 'total_mv']
        ]

    def collect_index_constituents(
        self,
        index_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        df = self.client.index_constituents(index_code)
        return self._normalize_constituents(
            df, 'index', index_code, as_of_date
        )

    def collect_industry_constituents(
        self,
        industry_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        df = self.client.industry_constituents(industry_code)
        return self._normalize_constituents(
            df, 'industry', industry_code, as_of_date
        )

    def _normalize_constituents(
        self,
        df: pd.DataFrame,
        group_type: str,
        group_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        if df is None or df.empty:
            return pd.DataFrame()
        code_column = next(
            (column for column in (
                '代码', '品种代码', '成分券代码', 'code', 'symbol'
            ) if column in df.columns),
            None,
        )
        if code_column is None:
            raise ValueError(f"成分股数据缺少代码列: {list(df.columns)}")
        return pd.DataFrame({
            'group_type': group_type,
            'group_code': group_code,
            'ts_code': df[code_column].map(to_ts_code),
            'as_of_date': pd.to_datetime(as_of_date).date(),
            'weight': None,
            'source': self.source_name,
        })
