"""
DailyCollectionJob 单元测试

验证任务状态流转、幂等性、异常处理。
"""

import unittest
from unittest.mock import MagicMock, patch
from datetime import date

import pandas as pd


class TestDailyCollectionJob(unittest.TestCase):
    """DailyCollectionJob 测试"""

    def _make_job(self, collector=None, stock_repo=None, task_repo=None):
        from app.jobs.daily_collection_job import DailyCollectionJob

        collector = collector or MagicMock()
        collector.source_name = 'tushare'
        stock_repo = stock_repo or MagicMock()
        task_repo = task_repo or MagicMock()
        task_repo.is_task_success.return_value = False
        task_repo.create_task.return_value = 1

        return DailyCollectionJob(collector, stock_repo, task_repo)

    def test_skip_non_trade_day(self):
        """非交易日自动跳过"""
        # 周六
        trade_calendar = set()  # 空日历，周六不在其中
        job = self._make_job()
        job.trade_calendar = trade_calendar

        result = job.execute('20260704')  # 2026-07-04 是周六
        self.assertTrue(result['skipped'])
        self.assertTrue(result['success'])

    def test_skip_already_success(self):
        """已成功任务跳过（幂等）"""
        from app.jobs.daily_collection_job import DailyCollectionJob

        collector = MagicMock()
        collector.source_name = 'tushare'

        stock_repo = MagicMock()

        task_repo = MagicMock()
        task_repo.is_task_success.return_value = True
        task_repo.create_task.return_value = 1

        job = DailyCollectionJob(collector, stock_repo, task_repo)

        # 验证 mock 设置
        assert task_repo.is_task_success('daily', '20260703', 'tushare') == True

        result = job.execute('20260703')
        self.assertTrue(result['skipped'], f"应该跳过但 skipped={result['skipped']}, error={result.get('error')}")
        self.assertTrue(result['success'])

    def test_successful_collection(self):
        """成功采集状态流转正确"""
        collector = MagicMock()
        collector.source_name = 'tushare'
        collector.collect_daily.return_value = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': [date(2026, 7, 3)],
            'open': [10.0],
            'high': [10.5],
            'low': [9.5],
            'close': [10.2],
            'pre_close': [9.8],
            'change': [0.4],
            'pct_chg': [4.08],
            'vol': [100000.0],
            'amount': [102000.0],
        })

        stock_repo = MagicMock()
        stock_repo.save_daily_records.return_value = 1

        task_repo = MagicMock()
        task_repo.is_task_success.return_value = False
        task_repo.create_task.return_value = 1

        from app.jobs.daily_collection_job import DailyCollectionJob
        job = DailyCollectionJob(collector, stock_repo, task_repo)

        result = job.execute('20260703')

        self.assertTrue(result['success'])
        self.assertEqual(result['record_count'], 1)
        self.assertFalse(result['skipped'])

        # 验证状态更新调用
        task_repo.update_status.assert_called()

    def test_failed_collection(self):
        """采集失败时任务状态为 FAILED"""
        collector = MagicMock()
        collector.source_name = 'tushare'
        collector.collect_daily.side_effect = Exception("网络超时")

        task_repo = MagicMock()
        task_repo.is_task_success.return_value = False
        task_repo.create_task.return_value = 1

        job = self._make_job(collector=collector, task_repo=task_repo)

        result = job.execute('20260703')

        self.assertFalse(result['success'])
        self.assertIsNotNone(result['error'])

    def test_empty_data_on_trade_day(self):
        """交易日返回空数据记录失败"""
        collector = MagicMock()
        collector.source_name = 'tushare'
        collector.collect_daily.return_value = pd.DataFrame()

        # 添加交易日到日历
        trade_cal = {date(2026, 7, 3)}

        task_repo = MagicMock()
        task_repo.is_task_success.return_value = False
        task_repo.create_task.return_value = 1

        from app.jobs.daily_collection_job import DailyCollectionJob
        job = DailyCollectionJob(collector, MagicMock(), task_repo, trade_calendar=trade_cal)

        result = job.execute('20260703')

        self.assertFalse(result['success'])


if __name__ == '__main__':
    unittest.main()
