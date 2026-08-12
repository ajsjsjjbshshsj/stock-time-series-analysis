"""
StockDaily 数据模型

定义统一的日线行情数据结构，用于在 Collector、Job、Repository 之间传递数据。
与数据库 ORM 模型解耦，方便测试和扩展。
"""

from dataclasses import dataclass, asdict
from datetime import date
from typing import Optional


@dataclass
class StockDailyRecord:
    """日线行情记录（统一格式）"""
    ts_code: str
    trade_date: date
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    pre_close: Optional[float] = None
    change: Optional[float] = None
    pct_chg: Optional[float] = None
    vol: Optional[float] = None
    amount: Optional[float] = None
    source: Optional[str] = None

    def to_dict(self) -> dict:
        """转为字典（用于数据库写入）。"""
        return asdict(self)


# 统一字段列表
STOCK_DAILY_COLUMNS = [
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
    'pre_close', 'change', 'pct_chg', 'vol', 'amount',
]

# 必填字段（非空校验）
STOCK_DAILY_REQUIRED = [
    'ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol',
]
