"""每日估值指标的独立采集任务。"""

from datetime import date
import math
from typing import Iterable, Optional

import pandas as pd

from app.models.collection_task import CollectionTaskRecord, TaskStatus, TaskType
from app.utils.date_utils import to_yyyymmdd
from app.utils.logger import get_logger

logger = get_logger(__name__)

DAILY_BASIC_COLUMNS = (
    'ts_code', 'trade_date', 'turnover_rate', 'pe', 'pe_ttm',
    'pb', 'ps', 'total_mv',
)


class DailyBasicCollectionJob:
    """采集一个交易日的 raw daily-basic 数据，不影响 OHLCV 任务。"""

    def __init__(self, collector, market_repo, task_repo):
        self.collector = collector
        self.market_repo = market_repo
        self.task_repo = task_repo

    def execute(self, trade_date: str, stock_codes: Optional[Iterable[str]] = None) -> dict:
        trade_date_clean = to_yyyymmdd(trade_date)
        source = self.collector.source_name
        result = {
            'success': False,
            'record_count': 0,
            'skipped': False,
            'error': None,
        }

        if self.task_repo.is_task_success(
            TaskType.DAILY_BASIC.value, trade_date_clean, source
        ):
            result['success'] = True
            result['skipped'] = True
            return result

        task_id = self.task_repo.create_task(CollectionTaskRecord(
            task_type=TaskType.DAILY_BASIC.value,
            business_date=trade_date_clean,
            source=source,
        ))
        self.task_repo.update_status(task_id, TaskStatus.RUNNING.value)

        try:
            frame = self._collect(trade_date_clean, stock_codes)
            records = self._normalize(frame, source)
            if not records:
                raise ValueError('每日指标采集返回空数据')

            count = self.market_repo.save_daily_basic(records)
            self.task_repo.update_status(
                task_id, TaskStatus.SUCCESS.value, record_count=count
            )
            result['success'] = True
            result['record_count'] = count
            return result
        except Exception as exc:
            logger.error('每日指标 %s 采集失败: %s', trade_date_clean, exc)
            self.task_repo.update_status(
                task_id,
                TaskStatus.FAILED.value,
                record_count=0,
                error_message=str(exc)[:500],
            )
            result['error'] = str(exc)
            return result

    def _collect(
        self,
        trade_date: str,
        stock_codes: Optional[Iterable[str]],
    ) -> pd.DataFrame:
        if stock_codes is None:
            return self.collector.collect_daily_basic(trade_date)

        frames = []
        for code in stock_codes:
            frame = self.collector.collect_daily_basic(trade_date, ts_code=code)
            if frame is not None and not frame.empty:
                frames.append(frame)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @staticmethod
    def _normalize(frame: pd.DataFrame, source: str) -> list[dict]:
        if frame is None or frame.empty:
            return []
        missing = [column for column in DAILY_BASIC_COLUMNS if column not in frame.columns]
        if missing:
            raise ValueError(f'每日指标数据缺少字段: {missing}')

        normalized = frame.loc[:, DAILY_BASIC_COLUMNS].copy()
        normalized = normalized.dropna(subset=['ts_code', 'trade_date'])
        normalized['trade_date'] = pd.to_datetime(normalized['trade_date']).dt.date
        normalized['source'] = source
        normalized = normalized.drop_duplicates(['ts_code', 'trade_date'], keep='last')

        records = normalized.to_dict('records')
        for record in records:
            for column in DAILY_BASIC_COLUMNS[2:]:
                value = record[column]
                if pd.isna(value) or (
                    isinstance(value, float) and not math.isfinite(value)
                ):
                    record[column] = None
        return records
