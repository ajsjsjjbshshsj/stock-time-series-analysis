"""
历史回补任务 (HistoryBackfillJob)

负责历史数据回补：

    1. 接收开始日期和结束日期
    2. 获取期间内所有交易日
    3. 按交易日逐日调用 DailyCollectionJob
    4. 记录每一天的执行结果
    5. 输出成功数量和失败数量

关键规则：
    - 某一天失败不会中断整个流程
    - 已成功采集的日期自动跳过（幂等）
    - 最后输出汇总报告
"""

import time
from typing import Optional, List

from app.collectors.base_collector import BaseCollector
from app.repositories.stock_repository import StockRepository
from app.repositories.task_repository import TaskRepository
from app.jobs.daily_collection_job import DailyCollectionJob
from app.outputs.base_output import BaseOutput
from app.utils.logger import get_logger
from app.utils.date_utils import get_trade_days, load_trade_calendar, parse_date, to_yyyymmdd

logger = get_logger(__name__)


class HistoryBackfillJob:
    """历史数据回补任务"""

    def __init__(
        self,
        collector: BaseCollector,
        stock_repo: StockRepository,
        task_repo: TaskRepository,
        stock_codes: Optional[List[str]] = None,
        output: Optional[BaseOutput] = None,
    ):
        """
        Args:
            collector: 数据采集器
            stock_repo: 股票数据仓库
            task_repo: 任务数据仓库
            stock_codes: 可选股票代码列表（逐股模式）
        """
        self.collector = collector
        self.stock_repo = stock_repo
        self.task_repo = task_repo
        self.stock_codes = stock_codes
        self.output = output

        # 加载交易日历
        self.trade_calendar = load_trade_calendar(collector)

    def execute(self, start_date: str, end_date: str) -> dict:
        """
        执行历史回补。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date: 结束日期 YYYYMMDD

        Returns:
            dict: {
                'success': bool,
                'total_trade_days': int,
                'success_count': int,
                'failed_count': int,
                'skipped_count': int,
                'total_records': int,
                'failed_dates': list[str],
                'elapsed': float,
            }
        """
        t0 = time.time()
        start_d = parse_date(start_date)
        end_d = parse_date(end_date)

        logger.info("=" * 60)
        logger.info(f"开始历史回补: {start_d} ~ {end_d}")
        logger.info(f"数据源: {self.collector.source_name}")
        logger.info("=" * 60)

        # 获取交易日列表
        trade_days = get_trade_days(start_d, end_d, self.trade_calendar)

        result = {
            'success': True,
            'total_trade_days': len(trade_days),
            'success_count': 0,
            'failed_count': 0,
            'skipped_count': 0,
            'total_records': 0,
            'failed_dates': [],
            'elapsed': 0.0,
        }

        if not trade_days:
            logger.warning(f"{start_d}~{end_d} 范围内无交易日")
            result['elapsed'] = time.time() - t0
            return result

        # 创建 DailyCollectionJob
        daily_job = DailyCollectionJob(
            collector=self.collector,
            stock_repo=self.stock_repo,
            task_repo=self.task_repo,
            trade_calendar=self.trade_calendar,
            output=self.output,
        )

        # 逐日执行
        for idx, td in enumerate(trade_days, 1):
            td_str = to_yyyymmdd(td)
            logger.info(f"[{idx}/{len(trade_days)}] 回补 {td_str}")

            try:
                day_result = daily_job.execute(td_str, self.stock_codes)

                if day_result['skipped']:
                    result['skipped_count'] += 1
                elif day_result['success']:
                    result['success_count'] += 1
                    result['total_records'] += day_result['record_count']
                else:
                    result['failed_count'] += 1
                    result['failed_dates'].append(td_str)
                    result['success'] = False

            except Exception as e:
                logger.error(f"回补 {td_str} 异常: {e}")
                result['failed_count'] += 1
                result['failed_dates'].append(td_str)
                result['success'] = False

        result['elapsed'] = time.time() - t0

        # 输出汇总
        logger.info("=" * 60)
        logger.info("历史回补汇总:")
        logger.info(f"  计划交易日: {result['total_trade_days']}")
        logger.info(f"  成功: {result['success_count']}")
        logger.info(f"  失败: {result['failed_count']}")
        logger.info(f"  跳过: {result['skipped_count']}")
        logger.info(f"  总写入行数: {result['total_records']}")
        logger.info(f"  总耗时: {result['elapsed']:.1f}s")
        if result['failed_dates']:
            logger.info(f"  失败日期: {', '.join(result['failed_dates'][:20])}")
            if len(result['failed_dates']) > 20:
                logger.info(f"  ... 等共 {len(result['failed_dates'])} 天")
        logger.info("=" * 60)

        return result
