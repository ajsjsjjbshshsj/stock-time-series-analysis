"""按 stock_daily 已有交易日补采 daily-basic。"""

import time

from app.config import COLLECTION_CONFIG
from app.jobs.daily_basic_collection_job import DailyBasicCollectionJob
from app.utils.date_utils import to_yyyymmdd


class DailyBasicBackfillJob:
    """有界、逐日、可恢复的每日指标历史补采。"""

    def __init__(
        self,
        collector,
        market_repo,
        task_repo,
        request_interval=None,
        stock_codes=None,
    ):
        self.collector = collector
        self.market_repo = market_repo
        self.task_repo = task_repo
        self.request_interval = (
            COLLECTION_CONFIG['request_interval']
            if request_interval is None else request_interval
        )
        self.stock_codes = stock_codes

    def execute(self, start_date: str, end_date: str) -> dict:
        dates = self.market_repo.list_stock_daily_trade_dates(start_date, end_date)
        result = {'success_count': 0, 'failed_count': 0, 'skipped_count': 0}
        daily_job = DailyBasicCollectionJob(
            self.collector, self.market_repo, self.task_repo
        )

        for index, trade_date in enumerate(dates):
            day_result = daily_job.execute(
                to_yyyymmdd(trade_date), stock_codes=self.stock_codes
            )
            if day_result['skipped']:
                result['skipped_count'] += 1
            elif day_result['success']:
                result['success_count'] += 1
            else:
                result['failed_count'] += 1

            if (
                not day_result['skipped']
                and index < len(dates) - 1
                and self.request_interval > 0
            ):
                time.sleep(self.request_interval)

        return result
