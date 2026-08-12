"""
数据采集服务 — CLI 入口

支持的命令：
    daily        采集指定交易日数据
    history      回补指定日期范围数据
    basic        更新股票基础信息
    retry-failed 重试失败任务

示例：
    python main.py daily --date 2026-07-03
    python main.py daily --date 2026-07-03 --source akshare
    python main.py history --start 2026-01-01 --end 2026-07-03
    python main.py basic
    python main.py retry-failed
"""

import argparse
import os
import sys

# ── 路径设置 ─────────────────────────────────────────────────
# 确保 app 包可被导入（将服务根目录加入 sys.path）
_APP_DIR = os.path.dirname(os.path.abspath(__file__))               # .../app/
_SERVICE_ROOT = os.path.dirname(_APP_DIR)                            # .../python-collector/
_PYTHON_SERVICES_ROOT = os.path.dirname(_SERVICE_ROOT)               # .../python-services/
_STOCK_ANALYSIS_ROOT = os.path.join(_PYTHON_SERVICES_ROOT, 'stock-analysis-app')

for _p in (_SERVICE_ROOT, _STOCK_ANALYSIS_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.config import (
    DATABASE_CONFIG,
    COLLECTOR_SOURCE,
    COLLECTOR_OUTPUT_MODE,
    COLLECTION_CONFIG,
    safe_config_repr,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── 数据库初始化 ─────────────────────────────────────────────

def _init_database():
    """
    初始化数据库连接并确保表存在。

    使用项目共享的 DatabaseConnector，同时确保 collection_task 表已创建。
    """
    from database.db_connector import DatabaseConnector
    from database.models import CollectionTask  # noqa: F401 — 确保 ORM 注册

    db = DatabaseConnector(DATABASE_CONFIG)
    db.create_tables()
    logger.info("数据库初始化完成")
    logger.info(f"配置: {safe_config_repr()}")
    return db


def _create_collector(source: str):
    """
    根据配置创建数据采集器。

    Args:
        source: 'tushare' 或 'akshare'

    Returns:
        BaseCollector 实例
    """
    if source == 'tushare':
        from app.collectors.tushare_collector import TushareCollector
        return TushareCollector()
    elif source == 'akshare':
        from app.collectors.akshare_collector import AkShareCollector
        return AkShareCollector()
    else:
        raise ValueError(f"不支持的数据源: {source}，可选: tushare, akshare")


# ── 子命令实现 ───────────────────────────────────────────────

def cmd_daily(args):
    """daily 子命令：采集指定交易日数据"""
    db = _init_database()
    source = args.source or COLLECTOR_SOURCE

    logger.info(f"执行单日采集: {args.date}, 数据源: {source}")

    collector = _create_collector(source)

    with db.session_scope() as session:
        from app.repositories.stock_repository import StockRepository
        from app.repositories.task_repository import TaskRepository
        from app.jobs.daily_collection_job import DailyCollectionJob
        from app.outputs.output_factory import OutputFactory
        from app.utils.date_utils import load_trade_calendar

        stock_repo = StockRepository(session)
        task_repo = TaskRepository(session)
        trade_calendar = load_trade_calendar(collector)
        output = OutputFactory.create(
            mode=COLLECTOR_OUTPUT_MODE,
            stock_repository=stock_repo,
        )

        logger.info(f"Daily output mode: {COLLECTOR_OUTPUT_MODE}")
        job = DailyCollectionJob(
            collector,
            stock_repo,
            task_repo,
            trade_calendar,
            output=output,
        )
        result = job.execute(args.date)

    if result['success']:
        if result['skipped']:
            logger.info(f"✓ {args.date} 已跳过（非交易日或已成功）")
        else:
            logger.info(
                f"✓ {args.date} 采集完成: {result['record_count']} 条, "
                f"耗时 {result['elapsed']:.1f}s"
            )
        return 0
    else:
        logger.error(f"✗ {args.date} 采集失败: {result.get('error', '未知')}")
        return 1


def cmd_history(args):
    """history 子命令：回补指定日期范围数据"""
    db = _init_database()
    source = args.source or COLLECTOR_SOURCE

    logger.info(f"执行历史回补: {args.start} ~ {args.end}, 数据源: {source}")

    collector = _create_collector(source)

    with db.session_scope() as session:
        from app.repositories.stock_repository import StockRepository
        from app.repositories.task_repository import TaskRepository
        from app.jobs.history_backfill_job import HistoryBackfillJob
        from app.outputs.output_factory import OutputFactory

        stock_repo = StockRepository(session)
        task_repo = TaskRepository(session)
        output = OutputFactory.create(
            mode=COLLECTOR_OUTPUT_MODE,
            stock_repository=stock_repo,
        )

        job = HistoryBackfillJob(
            collector,
            stock_repo,
            task_repo,
            output=output,
        )
        result = job.execute(args.start, args.end)

    if result['success']:
        logger.info("✓ 历史回补全部完成")
        return 0
    else:
        logger.warning(
            f"⚠ 历史回补部分完成: "
            f"{result['success_count']} 成功, "
            f"{result['failed_count']} 失败"
        )
        return 1


def cmd_basic(args):
    """basic 子命令：更新股票基础信息"""
    db = _init_database()
    source = args.source or COLLECTOR_SOURCE

    logger.info(f"更新股票基础信息, 数据源: {source}")

    collector = _create_collector(source)

    from app.models.collection_task import CollectionTaskRecord, TaskType, TaskStatus

    with db.session_scope() as session:
        from app.repositories.stock_repository import StockRepository
        from app.repositories.task_repository import TaskRepository

        stock_repo = StockRepository(session)
        task_repo = TaskRepository(session)

        # 创建任务记录
        today = __import__('datetime').datetime.now().strftime('%Y%m%d')
        task = CollectionTaskRecord(
            task_type=TaskType.BASIC.value,
            business_date=today,
            source=source,
        )
        task_id = task_repo.create_task(task)
        task_repo.update_status(task_id, TaskStatus.RUNNING.value)

        try:
            df = collector.collect_basic()
            if df.empty:
                task_repo.update_status(
                    task_id, TaskStatus.FAILED.value,
                    error_message="获取股票基础信息为空",
                )
                logger.error("获取股票基础信息为空")
                return 1

            # 标准化写入
            records = df.to_dict('records')
            count = stock_repo.save_stock_basic(records)

            task_repo.update_status(
                task_id, TaskStatus.SUCCESS.value,
                record_count=count,
            )
            logger.info(f"✓ 股票基础信息更新完成: {count} 只")
            return 0

        except Exception as e:
            task_repo.update_status(
                task_id, TaskStatus.FAILED.value,
                error_message=str(e)[:500],
            )
            logger.error(f"✗ 股票基础信息更新失败: {e}")
            return 1


def cmd_retry_failed(args):
    """retry-failed 子命令：重试失败任务"""
    db = _init_database()
    source = args.source or COLLECTOR_SOURCE

    logger.info(f"执行失败任务重试, 数据源: {source}")

    collector = _create_collector(source)

    with db.session_scope() as session:
        from app.repositories.stock_repository import StockRepository
        from app.repositories.task_repository import TaskRepository
        from app.jobs.retry_failed_job import RetryFailedJob
        from app.outputs.output_factory import OutputFactory

        stock_repo = StockRepository(session)
        task_repo = TaskRepository(session)
        output = OutputFactory.create(
            mode=COLLECTOR_OUTPUT_MODE,
            stock_repository=stock_repo,
        )

        job = RetryFailedJob(
            collector,
            stock_repo,
            task_repo,
            output=output,
        )
        result = job.execute()

    if result['success']:
        logger.info("✓ 失败任务重试完成")
        return 0
    else:
        logger.warning(
            f"⚠ 重试部分完成: "
            f"{result['recovered']} 恢复, "
            f"{result['still_failed']} 仍失败"
        )
        return 1


# ── 主函数 ───────────────────────────────────────────────────

def main():
    """采集服务 CLI 入口"""
    parser = argparse.ArgumentParser(
        description='数据采集服务 V0.2',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令说明:
  daily         采集指定交易日数据
  history       回补指定日期范围数据
  basic         更新股票基础信息
  retry-failed  重试失败任务

示例:
  python main.py daily --date 2026-07-03
  python main.py daily --date 2026-07-03 --source akshare
  python main.py history --start 2026-01-01 --end 2026-07-03
  python main.py basic
  python main.py retry-failed
  python main.py retry-failed --source tushare
        """,
    )

    # 全局参数
    parser.add_argument(
        '--source', type=str, default=None,
        choices=['tushare', 'akshare'],
        help='数据源（默认使用 COLLECTOR_SOURCE 环境变量）',
    )

    subparsers = parser.add_subparsers(dest='command', help='运行模式')

    # daily
    p_daily = subparsers.add_parser('daily', help='采集指定交易日数据')
    p_daily.add_argument(
        '--date', type=str, required=True,
        help='交易日期（YYYYMMDD 或 YYYY-MM-DD）',
    )

    # history
    p_history = subparsers.add_parser('history', help='回补历史数据')
    p_history.add_argument(
        '--start', type=str, required=True,
        help='开始日期（YYYYMMDD 或 YYYY-MM-DD）',
    )
    p_history.add_argument(
        '--end', type=str, required=True,
        help='结束日期（YYYYMMDD 或 YYYY-MM-DD）',
    )

    # basic
    subparsers.add_parser('basic', help='更新股票基础信息')

    # retry-failed
    subparsers.add_parser('retry-failed', help='重试失败任务')

    # 解析
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # 分发
    dispatch = {
        'daily': cmd_daily,
        'history': cmd_history,
        'basic': cmd_basic,
        'retry-failed': cmd_retry_failed,
    }

    handler = dispatch.get(args.command)
    if handler:
        exit_code = handler(args)
        sys.exit(exit_code or 0)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
