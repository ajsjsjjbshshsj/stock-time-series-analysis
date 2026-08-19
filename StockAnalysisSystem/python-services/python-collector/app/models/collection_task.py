"""
CollectionTask 数据模型

采集任务状态跟踪模型，对应 collection_task 表。

状态流转：
    PENDING → RUNNING → SUCCESS / FAILED
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from enum import Enum


class TaskStatus(str, Enum):
    """采集任务状态"""
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCESS = 'SUCCESS'
    PARTIAL_SUCCESS = 'PARTIAL_SUCCESS'
    FAILED = 'FAILED'


class TaskType(str, Enum):
    """采集任务类型"""
    DAILY = 'daily'
    DAILY_BASIC = 'daily_basic'
    CONSTITUENT = 'constituent'
    HISTORY = 'history'
    BASIC = 'basic'
    RETRY = 'retry'


@dataclass
class CollectionTaskRecord:
    """采集任务记录"""
    task_type: str          # daily / history / basic / retry
    business_date: str      # 业务日期或可唯一标识快照的业务键
    source: str             # tushare / akshare
    status: str = TaskStatus.PENDING.value
    record_count: int = 0
    retry_count: int = 0
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        """转为字典（用于数据库写入）。"""
        d = {}
        for k, v in self.__dict__.items():
            if v is not None or k in ('task_type', 'business_date', 'source', 'status'):
                d[k] = v
        return d

    @property
    def unique_key(self) -> str:
        """任务唯一标识：task_type + business_date + source"""
        return f"{self.task_type}:{self.business_date}:{self.source}"
