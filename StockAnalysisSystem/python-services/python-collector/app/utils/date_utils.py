"""
日期工具

负责：
    - 日期格式转换（YYYYMMDD ↔ YYYY-MM-DD ↔ date/datetime）
    - 交易日判断（基于交易日历）
    - 日期范围生成
    - Tushare 交易日历查询
"""

from datetime import datetime, date, timedelta
from typing import List, Optional

import pandas as pd

from app.utils.logger import get_logger

logger = get_logger(__name__)

# 缓存交易日历
_trade_calendar_cache: Optional[set] = None


def parse_date(date_str: str) -> date:
    """
    将多种格式的日期字符串解析为 date 对象。

    支持格式：
        YYYYMMDD, YYYY-MM-DD, YYYY/MM/DD

    Args:
        date_str: 日期字符串

    Returns:
        date 对象
    """
    date_str = date_str.strip().replace('/', '-')
    for fmt in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {date_str}，支持格式: YYYYMMDD, YYYY-MM-DD")


def to_yyyymmdd(d) -> str:
    """
    将 date/datetime/str 转为 YYYYMMDD 格式字符串。

    Args:
        d: date, datetime 或日期字符串

    Returns:
        YYYYMMDD 格式字符串
    """
    if isinstance(d, str):
        return d.replace('-', '').replace('/', '')
    if isinstance(d, datetime):
        return d.strftime('%Y%m%d')
    if isinstance(d, date):
        return d.strftime('%Y%m%d')
    return str(d).replace('-', '')


def to_date_str(d) -> str:
    """将 date/datetime/str 转为 YYYY-MM-DD 格式字符串。"""
    if isinstance(d, str):
        d = parse_date(d)
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime('%Y-%m-%d')


def today_str() -> str:
    """返回今天的 YYYYMMDD 格式字符串。"""
    return datetime.now().strftime('%Y%m%d')


def load_trade_calendar(tushare_api=None) -> set:
    """
    加载交易日历（使用 Tushare 或简单周末判断）。

    Args:
        tushare_api: 可选的 TushareAPI 实例

    Returns:
        set of date 对象（所有交易日）
    """
    global _trade_calendar_cache
    if _trade_calendar_cache is not None:
        return _trade_calendar_cache

    if tushare_api is not None:
        try:
            df = tushare_api.pro.trade_cal(
                exchange='SSE',
                start_date='20000101',
                end_date='20301231',
                fields='cal_date,is_open',
            )
            if not df.empty:
                trade_dates = set(
                    pd.to_datetime(df[df['is_open'] == 1]['cal_date']).dt.date
                )
                _trade_calendar_cache = trade_dates
                logger.info(f"从 Tushare 加载交易日历: {len(trade_dates)} 个交易日")
                return trade_dates
        except Exception as e:
            logger.warning(f"加载 Tushare 交易日历失败: {e}，降级为周末判断")

    # 降级：不缓存，使用 is_trade_day_simple 判断
    logger.info("使用简单周末判断模式（无交易日历缓存）")
    return set()


def is_trade_day(d, trade_calendar: Optional[set] = None) -> bool:
    """
    判断是否为交易日。

    Args:
        d: date/datetime/str
        trade_calendar: 交易日历 set，None 则使用简单周末判断

    Returns:
        True 如果是交易日
    """
    if isinstance(d, str):
        d = parse_date(d)
    elif isinstance(d, datetime):
        d = d.date()

    if trade_calendar:
        return d in trade_calendar

    # 简单判断：非周末即交易日（不含节假日）
    return d.weekday() < 5


def get_trade_days(start_date, end_date, trade_calendar: Optional[set] = None) -> List[date]:
    """
    获取日期范围内的所有交易日。

    Args:
        start_date: 开始日期（str/date/datetime）
        end_date: 结束日期（str/date/datetime）
        trade_calendar: 交易日历 set

    Returns:
        交易日 date 列表（升序）
    """
    if isinstance(start_date, str):
        start_date = parse_date(start_date)
    elif isinstance(start_date, datetime):
        start_date = start_date.date()

    if isinstance(end_date, str):
        end_date = parse_date(end_date)
    elif isinstance(end_date, datetime):
        end_date = end_date.date()

    days = []
    current = start_date
    while current <= end_date:
        if is_trade_day(current, trade_calendar):
            days.append(current)
        current += timedelta(days=1)

    logger.info(f"交易日范围: {start_date} ~ {end_date}，共 {len(days)} 个交易日")
    return days
