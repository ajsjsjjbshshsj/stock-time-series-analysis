"""按 stock_daily 已有交易日补采 daily-basic。"""

import time

from app.config import COLLECTION_CONFIG
from app.jobs.daily_basic_collection_job import DailyBasicCollectionJob
from app.utils.date_utils import to_yyyymmdd
from app.utils.logger import get_logger

logger = get_logger(__name__)


class DailyBasicBackfillJob:
    """有界、逐日、可恢复的每日指标历史补采。"""

    def __init__(
        self,
        collector,
        market_repo,
        task_repo,
        request_interval=None,
        stock_codes=None,
        checkpoint=None,
    ):
        self.collector = collector
        self.market_repo = market_repo
        self.task_repo = task_repo
        self.request_interval = (
            COLLECTION_CONFIG['request_interval']
            if request_interval is None else request_interval
        )
        self.stock_codes = stock_codes
        self.checkpoint = checkpoint

    def execute(self, start_date: str, end_date: str) -> dict:
        dates = self.market_repo.list_stock_daily_trade_dates(start_date, end_date)
        result = {'success_count': 0, 'failed_count': 0, 'skipped_count': 0}
        daily_job = DailyBasicCollectionJob(
            self.collector, self.market_repo, self.task_repo
        )

        for index, trade_date in enumerate(dates):
            trade_date_clean = to_yyyymmdd(trade_date)
            logger.info(
                'daily-basic 补采进度 %s/%s: %s',
                index + 1, len(dates), trade_date_clean,
            )
            day_result = daily_job.execute(
                trade_date_clean, stock_codes=self.stock_codes
            )
            if day_result['skipped']:
                result['skipped_count'] += 1
            elif day_result['success']:
                result['success_count'] += 1
            else:
                result['failed_count'] += 1

            if self.checkpoint is not None:
                self.checkpoint()

            if (
                not day_result['skipped']
                and index < len(dates) - 1
                and self.request_interval > 0
            ):
                time.sleep(self.request_interval)

        return result
