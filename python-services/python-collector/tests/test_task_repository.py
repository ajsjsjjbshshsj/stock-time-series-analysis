"""
TaskRepository 单元测试

验证任务创建、状态更新、失败查询等功能。
"""

import unittest
from unittest.mock import MagicMock

from app.models.collection_task import CollectionTaskRecord, TaskStatus, TaskType


class TestTaskRepository(unittest.TestCase):
    """TaskRepository 测试"""

    def _make_repo(self):
        from app.repositories.task_repository import TaskRepository
        session = MagicMock()
        return TaskRepository(session), session

    def test_create_task(self):
        """创建任务记录"""
        repo, session = self._make_repo()

        # 模拟无已存在记录
        session.execute.return_value = MagicMock(fetchone=MagicMock(return_value=None))

        task = CollectionTaskRecord(
            task_type=TaskType.DAILY.value,
            business_date='20260703',
            source='tushare',
        )

        # 第一次查无记录
        mock_result_find = MagicMock()
        mock_result_find.fetchone.return_value = None
        # 创建后查 ID
        mock_result_id = MagicMock()
        mock_result_id.fetchone.return_value = (42,)

        session.execute.side_effect = [mock_result_find, None, mock_result_id]

        task_id = repo.create_task(task)
        self.assertEqual(task_id, 42)

    def test_is_task_success_true(self):
        """查询已成功任务"""
        repo, session = self._make_repo()

        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            1, 'daily', '20260703', 'tushare', 'SUCCESS', 100, 0, None,
        )
        session.execute.return_value = mock_result

        result = repo.is_task_success('daily', '20260703', 'tushare')
        self.assertTrue(result)

    def test_is_task_success_false(self):
        """查询未成功任务"""
        repo, session = self._make_repo()

        mock_result = MagicMock()
        mock_result.fetchone.return_value = (
            1, 'daily', '20260703', 'tushare', 'FAILED', 0, 1, 'error msg',
        )
        session.execute.return_value = mock_result

        result = repo.is_task_success('daily', '20260703', 'tushare')
        self.assertFalse(result)

    def test_get_failed_tasks(self):
        """查询失败任务列表"""
        repo, session = self._make_repo()

        mock_result = [
            (1, 'daily', '20260701', 'tushare', 'FAILED', 0, 2, 'timeout', None, None),
            (2, 'daily', '20260702', 'tushare', 'FAILED', 0, 1, 'network error', None, None),
        ]
        session.execute.return_value = mock_result

        tasks = repo.get_failed_tasks()
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]['business_date'], '20260701')
        self.assertEqual(tasks[1]['error_message'], 'network error')

    def test_update_status_running(self):
        """更新状态为 RUNNING"""
        repo, session = self._make_repo()

        repo.update_status(1, TaskStatus.RUNNING.value)
        session.execute.assert_called_once()

    def test_update_status_success(self):
        """更新状态为 SUCCESS 含采集数量"""
        repo, session = self._make_repo()

        repo.update_status(1, TaskStatus.SUCCESS.value, record_count=100)
        session.execute.assert_called_once()

    def test_update_status_failed(self):
        """更新状态为 FAILED 含错误信息"""
        repo, session = self._make_repo()

        repo.update_status(1, TaskStatus.FAILED.value, error_message="网络超时")
        session.execute.assert_called_once()

    def test_update_status_partial_success_keeps_delivery_error(self):
        repo, session = self._make_repo()

        repo.update_status(
            1,
            TaskStatus.PARTIAL_SUCCESS.value,
            record_count=100,
            error_message="KAFKA: broker unavailable",
        )

        _, params = session.execute.call_args.args
        statement = str(session.execute.call_args.args[0])
        self.assertEqual(params['status'], 'PARTIAL_SUCCESS')
        self.assertEqual(params['record_count'], 100)
        self.assertEqual(
            params['error_message'],
            'KAFKA: broker unavailable',
        )
        self.assertIn('retry_count = retry_count + 1', statement)

    def test_get_failed_tasks_includes_partial_success(self):
        repo, session = self._make_repo()
        session.execute.return_value = []

        repo.get_failed_tasks()

        _, params = session.execute.call_args.args
        self.assertEqual(params['failed_status'], 'FAILED')
        self.assertEqual(params['partial_status'], 'PARTIAL_SUCCESS')


if __name__ == '__main__':
    unittest.main()
