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
}


def _to_tushare_code(code: str) -> str:
    """将 6 位代码转为 Tushare 格式（统一 ts_code 风格）。"""
    code = str(code).strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    return f"{code}.SZ"


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
    ts_code = _to_tushare_code(symbol)
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
    ]
    for col in standard_cols:
        if col not in df.columns:
            df[col] = None

    return df[standard_cols]


class AkShareCollector(BaseCollector):
    """AkShare 数据采集器"""

    def __init__(self):
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

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )

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
        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            logger.warning("AkShare 获取股票信息为空")
            return pd.DataFrame()

        # 标准化列名
        result = pd.DataFrame()
        if '代码' in df.columns:
            result['symbol'] = df['代码']
            result['ts_code'] = df['代码'].apply(_to_tushare_code)
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
