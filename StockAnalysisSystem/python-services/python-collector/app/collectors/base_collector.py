"""
采集器基类 — 定义统一接口

所有数据源采集器（Tushare、AkShare）都继承此基类，
保证返回相同格式的 DataFrame。

职责：
    - 调用外部数据源
    - 返回统一格式数据

不负责：
    - 写数据库
    - 更新任务状态
    - 计算技术指标
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional

import pandas as pd


class BaseCollector(ABC):
    """
    采集器抽象基类。

    子类必须实现以下方法：
        - collect_daily(trade_date) → DataFrame
        - collect_basic() → DataFrame
        - collect_trade_calendar(start, end) → DataFrame
    """

    @abstractmethod
    def collect_daily(self, trade_date: str) -> pd.DataFrame:
        """
        采集指定交易日的全市场行情数据。

        Args:
            trade_date: 交易日期，YYYYMMDD 格式

        Returns:
            DataFrame，统一包含以下列：
                ts_code     : 股票代码（Tushare 格式，如 000001.SZ）
                trade_date  : 交易日期（date 类型）
                open        : 开盘价
                high        : 最高价
                low         : 最低价
                close       : 收盘价
                pre_close   : 昨收价
                change      : 涨跌额
                pct_chg     : 涨跌幅
                vol         : 成交量（手）
                amount      : 成交额（千元）

            返回空 DataFrame 表示当日无数据。
        """
        ...

    @abstractmethod
    def collect_daily_single(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        采集单只股票在日期范围内的日线数据。

        Args:
            ts_code: 股票代码（Tushare 格式或 6 位数字）
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame，列同 collect_daily
        """
        ...

    @abstractmethod
    def collect_basic(self) -> pd.DataFrame:
        """
        采集股票基本信息。

        Returns:
            DataFrame，统一包含以下列：
                ts_code     : 股票代码
                symbol      : 简称代码
                name        : 股票名称
                area        : 地域（可选）
                industry    : 行业（可选）
                list_date   : 上市日期（可选）
        """
        ...

    @abstractmethod
    def collect_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        获取交易日历。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            DataFrame，包含 cal_date, is_open 列
        """
        ...

    def collect_daily_basic(
        self,
        trade_date: str,
        ts_code: Optional[str] = None,
    ) -> pd.DataFrame:
        """Collect source-provided daily valuation data when available."""
        return pd.DataFrame()

    def collect_index_constituents(
        self,
        index_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        """Collect an index-membership snapshot when supported."""
        return pd.DataFrame()

    def collect_industry_constituents(
        self,
        industry_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        """Collect an industry-membership snapshot when supported."""
        return pd.DataFrame()

    @property
    @abstractmethod
    def source_name(self) -> str:
        """数据源名称标识，如 'tushare' / 'akshare'。"""
        ...
