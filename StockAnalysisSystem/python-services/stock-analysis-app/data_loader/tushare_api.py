"""
Tushare 与 Akshare API 封装

本模块只负责调用外部数据接口（Tushare / Akshare），不包含：
- 数据库读写
- 技术指标计算
- 采集流程控制

接口调用失败时抛出异常，由上层（collector）统一处理重试逻辑。
"""

import pandas as pd
import tushare as ts
import akshare as ak
from config.settings import TUSHARE_TOKEN
from config.logging_config import get_logger

logger = get_logger(__name__)


def to_tushare_code(code):
    """
    将 6 位股票代码转换为 Tushare 格式（如 000001 -> 000001.SZ）。

    Args:
        code: 股票代码，可以是 6 位数字或已带后缀的格式

    Returns:
        str: Tushare 格式的股票代码
    """
    code = str(code).strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    return f"{code}.SZ"


class TushareAPI:
    """Tushare API 封装 — 纯接口调用，异常向上抛出"""

    def __init__(self, token=None):
        self.token = token or TUSHARE_TOKEN
        ts.set_token(self.token)
        self.pro = ts.pro_api()
        logger.info("Tushare API 初始化成功")

    def get_stock_basic(self):
        """
        获取股票基本信息。

        Returns:
            DataFrame: 含 ts_code, symbol, name, area, industry, list_date
        """
        df = self.pro.stock_basic(
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name,area,industry,list_date'
        )
        logger.info(f"Tushare 获取股票基本信息: {len(df)} 只")
        return df

    def get_daily_data(self, ts_code, start_date, end_date):
        """
        获取个股日线数据（合并 daily + daily_basic）。

        Args:
            ts_code: 股票代码（支持 6 位数字或 Tushare 格式）
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)

        Returns:
            DataFrame: 日线数据（可能为空）
        """
        ts_code = to_tushare_code(ts_code)

        df = self.pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df.empty:
            return pd.DataFrame()

        df = df[[
            'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        ]]

        # 合并 daily_basic（估值、换手率等）
        df2 = self.pro.daily_basic(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if not df2.empty:
            df2 = df2[[
                'ts_code', 'trade_date', 'turnover_rate',
                'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
            ]]
            df = pd.merge(df, df2, on=['ts_code', 'trade_date'], how='left')

        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date')
        logger.info(f"Tushare {ts_code} 日线数据: {len(df)} 条")
        return df

    def get_stock_list(self):
        """
        获取所有 A 股列表。

        Returns:
            DataFrame: 含 ts_code, symbol, name
        """
        df = self.pro.stock_basic(
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name',
        )
        logger.info(f"Tushare 获取 A 股列表: {len(df)} 只")
        return df

    def get_index_constituents(self, index_code):
        """
        获取指数成分股列表（Tushare index_weight）。

        Args:
            index_code: 指数代码，如 "000300" / "000905" / "000300.SH"

        Returns:
            DataFrame: 成分股列表，含 con_code, code, symbol 列
        """
        raw_code = str(index_code).strip()
        if '.' in raw_code:
            candidate_codes = [raw_code]
        else:
            tushare_index_alias = {
                '000300': ['399300.SZ', '000300.SH'],
                '000905': ['000905.SH', '399905.SZ'],
                '000852': ['000852.SH'],
                '000016': ['000016.SH'],
                '000688': ['000688.SH'],
                '399006': ['399006.SZ'],
                '399001': ['399001.SZ'],
            }
            candidate_codes = tushare_index_alias.get(
                raw_code, [f"{raw_code}.SH", f"{raw_code}.SZ"]
            )

        end_date = pd.Timestamp.today().strftime('%Y%m%d')
        start_date = (pd.Timestamp.today() - pd.DateOffset(years=2)).strftime('%Y%m%d')

        for code in candidate_codes:
            df = self.pro.index_weight(
                index_code=code,
                start_date=start_date,
                end_date=end_date,
            )
            if not df.empty:
                df = df.sort_values('trade_date').drop_duplicates('con_code', keep='last')
                df['code'] = df['con_code'].astype(str).str[:6]
                df['symbol'] = df['code']
                logger.info(f"Tushare 获取指数 [{code}] 成分股 {len(df)} 只")
                return df
            logger.warning(f"Tushare 指数 [{code}] 无成分股数据")

        return pd.DataFrame()


class AkshareAPI:
    """Akshare API 封装（免费，无需 token） — 纯接口调用，异常向上抛出"""

    def __init__(self):
        logger.info("Akshare API 初始化成功")

    def get_stock_zh_a_hist(self, symbol, period="daily",
                            start_date="20200101", end_date=None):
        """
        获取 A 股历史行情数据。

        Args:
            symbol: 股票代码（如 000001）
            period: 周期 (daily/weekly/monthly)
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            DataFrame: 历史行情数据（中文列名）
        """
        if end_date is None:
            from datetime import datetime
            end_date = datetime.now().strftime("%Y%m%d")

        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust="qfq",
        )
        logger.info(f"Akshare {symbol} 历史数据: {len(df)} 条")
        return df

    def get_stock_info(self):
        """
        获取 A 股基本信息。

        Returns:
            DataFrame: 含 代码, 名称 等列
        """
        df = ak.stock_info_a_code_name()
        logger.info(f"Akshare 获取股票信息: {len(df)} 只")
        return df

    def get_industry_stocks(self, industry_name):
        """
        获取东方财富行业板块成分股。

        Args:
            industry_name: 行业板块名称，如 "银行", "医药", "电子"

        Returns:
            DataFrame: 成分股列表
        """
        df = ak.stock_board_industry_cons_em(symbol=industry_name)
        logger.info(f"Akshare 获取行业 [{industry_name}] 成分股 {len(df)} 只")
        return df

    def get_index_constituents(self, index_code):
        """
        获取指数成分股列表。

        Args:
            index_code: 指数代码，如 "000300", "000905"

        Returns:
            DataFrame: 成分股列表
        """
        df = ak.index_stock_cons(symbol=index_code)
        logger.info(f"Akshare 获取指数 [{index_code}] 成分股 {len(df)} 只")
        return df