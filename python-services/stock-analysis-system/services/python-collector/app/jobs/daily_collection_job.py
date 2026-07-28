"""
单日采集任务 (DailyCollectionJob)

编排一个完整交易日的数据采集流程：

    1. 判断是否为交易日
    2. 检查任务是否已成功执行（幂等）
    3. 创建或更新 collection_task
    4. 调用 Collector 采集数据
    5. 检查返回结果
    6. 调用 StockRepository 写入数据库
    7. 更新任务状态

失败流程：
    1. 按重试策略重试
    2. 仍然失败 → 任务状态 FAILED
    3. 记录错误原因和重试次数
"""

import time
import math
from datetime import datetime, date
from typing import Optional, List

import pandas as pd

from app.collectors.base_collector import BaseCollector
from app.repositories.stock_repository import StockRepository
from app.repositories.task_repository import TaskRepository
from app.models.collection_task import CollectionTaskRecord, TaskStatus, TaskType
from app.models.stock_daily import STOCK_DAILY_REQUIRED
from app.config import COLLECTION_CONFIG
from app.utils.logger import get_logger
from app.utils.date_utils import is_trade_day, to_yyyymmdd, load_trade_calendar
from app.utils.retry import retry_with_backoff, is_retryable

logger = get_logger(__name__)


def _safe_float(val):
    """将值转为 float，NaN/Inf 返回 None（MySQL 不接受 NaN）。"""
    try:
        v = float(val)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def _validate_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """
    校验并清洗 DataFrame：
    - 去除必填字段为空的行
    - 转换可选字段为安全浮点数
    """
    if df.empty:
        return df

    # 检查必填列是否存在
    missing = [c for c in STOCK_DAILY_REQUIRED if c not in df.columns]
    if missing:
        logger.warning(f"数据缺少必填列: {missing}，跳过校验")
        return df

    # 去除 open/high/low/close/vol 为 NaN 的行
    df = df.dropna(subset=['open', 'high', 'low', 'close', 'vol'])

    # 安全转换可选字段
    optional_float_cols = ['pre_close', 'change', 'pct_chg', 'amount']
    for col in optional_float_cols:
        if col in df.columns:
            df[col] = df[col].apply(_safe_float)
            # 过滤可选字段为 None 的行（保持与原项目一致）
            df = df[df[col].notna()]

    return df


class DailyCollectionJob:
    """单日数据采集任务"""

    def __init__(
        self,
        collector: BaseCollector,
        stock_repo: StockRepository,
        task_repo: TaskRepository,
        trade_calendar: Optional[set] = None,
    ):
        """
        Args:
            collector: 数据采集器实例
            stock_repo: 股票数据仓库
            task_repo: 任务数据仓库
            trade_calendar: 交易日历 set（date 对象集合）
        """
        self.collector = collector
        self.stock_repo = stock_repo
        self.task_repo = task_repo
        self.trade_calendar = trade_calendar

    def execute(self, trade_date: str, stock_codes: Optional[List[str]] = None) -> dict:
        """
        执行单日采集任务。

        Args:
            trade_date: 交易日期 YYYYMMDD
            stock_codes: 可选股票代码列表，None 则使用全市场按日期采集

        Returns:
            dict: {
                'success': bool,
                'record_count': int,
                'skipped': bool,
                'error': Optional[str],
                'elapsed': float,
            }
        """
        t0 = time.time()
        trade_date_clean = to_yyyymmdd(trade_date)
        source = self.collector.source_name

        result = {
            'success': False,
            'record_count': 0,
            'skipped': False,
            'error': None,
            'elapsed': 0.0,
        }

        # ── Step 1: 交易日判断 ──
        if self.trade_calendar is not None:
            try:
                d = date(
                    int(trade_date_clean[:4]),
                    int(trade_date_clean[4:6]),
                    int(trade_date_clean[6:8]),
                )
                if not is_trade_day(d, self.trade_calendar):
                    logger.info(f"{trade_date_clean} 非交易日，跳过")
                    result['skipped'] = True
                    result['success'] = True
                    result['elapsed'] = time.time() - t0
                    return result
            except ValueError:
                pass

        # ── Step 2: 幂等检查 ──
        if self.task_repo.is_task_success(TaskType.DAILY.value, trade_date_clean, source):
            logger.info(f"{trade_date_clean} 已成功采集，跳过（幂等）")
            result['skipped'] = True
            result['success'] = True
            result['elapsed'] = time.time() - t0
            return result

        # ── Step 3: 创建任务记录 ──
        task = CollectionTaskRecord(
            task_type=TaskType.DAILY.value,
            business_date=trade_date_clean,
            source=source,
        )
        task_id = self.task_repo.create_task(task)
        self.task_repo.update_status(task_id, TaskStatus.RUNNING.value)

        # ── Step 4: 调用 Collector 采集数据 ──
        try:
            if stock_codes:
                # 逐股采集模式
                df = self._collect_by_stocks(trade_date_clean, stock_codes)
            else:
                # 按日期全市场采集（Tushare 支持，AkShare 降级为逐股）
                df = self._collect_by_date(trade_date_clean)

            # ── Step 5: 校验数据 ──
            if df.empty:
                logger.warning(f"{trade_date_clean} 采集返回空数据")
                # 交易日返回空数据应记录警告
                self.task_repo.update_status(
                    task_id, TaskStatus.FAILED.value,
                    record_count=0,
                    error_message="交易日返回空数据",
                )
                result['error'] = "交易日返回空数据"
                result['elapsed'] = time.time() - t0
                return result

            df = _validate_dataframe(df)
            if df.empty:
                self.task_repo.update_status(
                    task_id, TaskStatus.FAILED.value,
                    record_count=0,
                    error_message="数据校验后为空",
                )
                result['error'] = "数据校验后为空"
                result['elapsed'] = time.time() - t0
                return result

            # ── Step 6: 写入数据库 ──
            records = df.to_dict('records')
            count = self.stock_repo.save_daily_records(records)

            # ── Step 7: 更新状态 ──
            self.task_repo.update_status(
                task_id, TaskStatus.SUCCESS.value,
                record_count=count,
            )

            result['success'] = True
            result['record_count'] = count
            result['elapsed'] = time.time() - t0

            logger.info(
                f"✓ {trade_date_clean} 采集成功: {count} 条, "
                f"耗时 {result['elapsed']:.1f}s"
            )
            return result

        except Exception as e:
            logger.error(f"✗ {trade_date_clean} 采集失败: {e}")
            self.task_repo.update_status(
                task_id, TaskStatus.FAILED.value,
                record_count=0,
                error_message=str(e)[:500],
            )
            result['error'] = str(e)
            result['elapsed'] = time.time() - t0
            return result

    def _collect_by_date(self, trade_date: str) -> pd.DataFrame:
        """按日期采集全市场（Tushare daily(trade_date=xxx) 接口）。"""
        retry_times = COLLECTION_CONFIG['retry_times']
        retry_delay = COLLECTION_CONFIG['retry_delay']

        @retry_with_backoff(max_retries=retry_times, base_delay=retry_delay)
        def _do_collect():
            return self.collector.collect_daily(trade_date)

        return _do_collect()

    def _collect_by_stocks(self, trade_date: str, stock_codes: List[str]) -> pd.DataFrame:
        """逐股采集模式。"""
        delay = COLLECTION_CONFIG['request_interval']
        all_dfs = []
        fail_count = 0

        for idx, code in enumerate(stock_codes, 1):
            try:
                df = self.collector.collect_daily_single(code, trade_date, trade_date)
                if df is not None and not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                fail_count += 1
                if fail_count <= 5:
                    logger.warning(f"{code} 采集失败: {e}")

            if idx < len(stock_codes):
                time.sleep(delay)

            if idx % 200 == 0:
                logger.info(f"逐股采集进度: {idx}/{len(stock_codes)}")

        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
