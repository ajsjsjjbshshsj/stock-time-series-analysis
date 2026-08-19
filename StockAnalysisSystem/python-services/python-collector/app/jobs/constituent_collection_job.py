"""指数和行业成分股快照采集任务。"""

import pandas as pd

from app.models.collection_task import CollectionTaskRecord, TaskStatus, TaskType
from app.utils.date_utils import to_yyyymmdd
from app.utils.logger import get_logger

logger = get_logger(__name__)

CONSTITUENT_COLUMNS = (
    'group_type', 'group_code', 'ts_code', 'as_of_date', 'weight', 'source'
)


class ConstituentCollectionJob:
    """保存一个指数或行业在指定日期的成分股快照。"""

    def __init__(self, collector, market_repo, task_repo):
        self.collector = collector
        self.market_repo = market_repo
        self.task_repo = task_repo

    def execute(self, group_type: str, group_code: str, as_of_date: str) -> dict:
        if group_type not in {'index', 'industry'}:
            raise ValueError("group_type must be 'index' or 'industry'")

        date_clean = to_yyyymmdd(as_of_date)
        source = self.collector.source_name
        business_key = f'{group_type}:{group_code}:{date_clean}'
        result = {
            'success': False,
            'record_count': 0,
            'skipped': False,
            'error': None,
        }

        if self.task_repo.is_task_success(
            TaskType.CONSTITUENT.value, business_key, source
        ):
            result['success'] = True
            result['skipped'] = True
            return result

        task_id = self.task_repo.create_task(CollectionTaskRecord(
            task_type=TaskType.CONSTITUENT.value,
            business_date=business_key,
            source=source,
        ))
        self.task_repo.update_status(task_id, TaskStatus.RUNNING.value)

        try:
            if group_type == 'index':
                frame = self.collector.collect_index_constituents(
                    group_code, date_clean
                )
            else:
                frame = self.collector.collect_industry_constituents(
                    group_code, date_clean
                )
            records = self._normalize(
                frame, group_type, group_code, date_clean, source
            )
            if not records:
                raise ValueError('成分股采集返回空数据')

            count = self.market_repo.replace_constituents(records)
            self.task_repo.update_status(
                task_id, TaskStatus.SUCCESS.value, record_count=count
            )
            result['success'] = True
            result['record_count'] = count
            return result
        except Exception as exc:
            logger.error('成分股 %s 采集失败: %s', business_key, exc)
            self.task_repo.update_status(
                task_id,
                TaskStatus.FAILED.value,
                record_count=0,
                error_message=str(exc)[:500],
            )
            result['error'] = str(exc)
            return result

    @staticmethod
    def _normalize(frame, group_type, group_code, date_clean, source):
        if frame is None or frame.empty or 'ts_code' not in frame.columns:
            return []
        normalized = pd.DataFrame({
            'group_type': group_type,
            'group_code': group_code,
            'ts_code': frame['ts_code'],
            'as_of_date': pd.to_datetime(date_clean).date(),
            'weight': frame['weight'] if 'weight' in frame.columns else None,
            'source': source,
        })
        normalized = normalized.dropna(subset=['ts_code'])
        normalized = normalized.drop_duplicates('ts_code', keep='last')
        records = normalized.loc[:, CONSTITUENT_COLUMNS].to_dict('records')
        for record in records:
            if pd.isna(record['weight']):
                record['weight'] = None
        return records
