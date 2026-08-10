"""
失败重试任务 (RetryFailedJob)

负责读取失败任务并重新执行：

    1. 查询 collection_task 中 FAILED 任务
    2. 按照日期和数据源重新执行
    3. 成功后更新为 SUCCESS
    4. 失败时增加 retry_count
    5. 保留最新错误信息
    6. 超过最大重试次数的任务跳过

关键规则：
    - 设置最大重试次数，避免无限循环
    - 仅重试 daily 类型的失败任务
"""

import time
from typing import Optional, List

from app.collectors.base_collector import BaseCollector
from app.repositories.stock_repository import StockRepository
from app.repositories.task_repository import TaskRepository
from app.models.collection_task import TaskType
from app.jobs.daily_collection_job import DailyCollectionJob
from app.outputs.base_output import BaseOutput
from app.config import COLLECTION_CONFIG
from app.utils.logger import get_logger
from app.utils.date_utils import load_trade_calendar

logger = get_logger(__name__)


class RetryFailedJob:
    """失败任务重试"""

    def __init__(
        self,
        collector: BaseCollector,
        stock_repo: StockRepository,
        task_repo: TaskRepository,
        stock_codes: Optional[List[str]] = None,
        output: Optional[BaseOutput] = None,
    ):
        self.collector = collector
        self.stock_repo = stock_repo
        self.task_repo = task_repo
        self.stock_codes = stock_codes
        self.output = output
        self.max_retry_count = COLLECTION_CONFIG.get('max_retry_count', 5)
        self.trade_calendar = load_trade_calendar(collector)

    def execute(self) -> dict:
        """
        执行失败任务重试。

        Returns:
            dict: {
                'success': bool,
                'total_failed': int,
                'retried': int,
                'recovered': int,
                'still_failed': int,
                'skipped_max_retry': int,
                'elapsed': float,
            }
        """
        t0 = time.time()

        logger.info("=" * 60)
        logger.info("开始失败任务重试")
        logger.info(f"数据源: {self.collector.source_name}")
        logger.info(f"最大重试次数: {self.max_retry_count}")
        logger.info("=" * 60)

        # 查询失败任务
        failed_tasks = self.task_repo.get_failed_tasks(limit=500)

        # 仅处理与当前数据源匹配的任务
        source = self.collector.source_name
        matching = [t for t in failed_tasks if t['source'] == source]

        result = {
            'success': True,
            'total_failed': len(matching),
            'retried': 0,
            'recovered': 0,
            'still_failed': 0,
            'skipped_max_retry': 0,
            'elapsed': 0.0,
        }

        if not matching:
            logger.info("无失败任务需要重试")
            result['elapsed'] = time.time() - t0
            return result

        logger.info(f"发现 {len(matching)} 个失败任务（数据源: {source}）")

        # 创建 DailyCollectionJob
        daily_job = DailyCollectionJob(
            collector=self.collector,
            stock_repo=self.stock_repo,
            task_repo=self.task_repo,
            trade_calendar=self.trade_calendar,
            output=self.output,
        )

        for task_info in matching:
            task_id = task_info['id']
            business_date = task_info['business_date']
            retry_count = task_info.get('retry_count', 0)

            # 检查重试次数上限
            if retry_count >= self.max_retry_count:
                logger.info(
                    f"跳过 {business_date}: 已重试 {retry_count} 次，"
                    f"超过上限 {self.max_retry_count}"
                )
                result['skipped_max_retry'] += 1
                continue

            logger.info(f"重试 {business_date} (已重试 {retry_count} 次)")

            try:
                # 重置任务状态为重试中
                self.task_repo.update_status(task_id, 'RUNNING')

                day_result = daily_job.execute(business_date, self.stock_codes)
                result['retried'] += 1

                if day_result['success'] and not day_result['skipped']:
                    result['recovered'] += 1
                    logger.info(f"✓ {business_date} 重试成功: {day_result['record_count']} 条")
                elif day_result['skipped']:
                    result['recovered'] += 1
                else:
                    result['still_failed'] += 1
                    result['success'] = False
                    logger.warning(
                        f"✗ {business_date} 重试失败: {day_result.get('error', '未知')}"
                    )

            except Exception as e:
                result['retried'] += 1
                result['still_failed'] += 1
                result['success'] = False
                logger.error(f"重试 {business_date} 异常: {e}")
                self.task_repo.update_status(
                    task_id, 'FAILED', error_message=str(e)[:500]
                )

        result['elapsed'] = time.time() - t0

        # 输出汇总
        logger.info("=" * 60)
        logger.info("失败重试汇总:")
        logger.info(f"  总失败任务: {result['total_failed']}")
        logger.info(f"  已重试: {result['retried']}")
        logger.info(f"  恢复成功: {result['recovered']}")
        logger.info(f"  仍然失败: {result['still_failed']}")
        logger.info(f"  超过重试上限: {result['skipped_max_retry']}")
        logger.info(f"  耗时: {result['elapsed']:.1f}s")
        logger.info("=" * 60)

        return result
