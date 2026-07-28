"""
采集任务访问层 (TaskRepository)

只负责 collection_task 表的数据库读写：
    - 创建采集任务
    - 更新任务状态
    - 更新采集行数
    - 记录失败原因
    - 查询失败任务
    - 判断任务是否已成功执行

不负责：
    - 调用外部数据接口
    - 控制采集流程
"""

from datetime import datetime
from typing import List, Optional, Dict

from sqlalchemy import text

from app.models.collection_task import TaskStatus, CollectionTaskRecord
from app.utils.logger import get_logger

logger = get_logger(__name__)

TABLE_COLLECTION_TASK = 'collection_task'


class TaskRepository:
    """采集任务数据访问仓库"""

    def __init__(self, session):
        """
        Args:
            session: SQLAlchemy Session 实例
        """
        self.session = session

    def create_task(self, task: CollectionTaskRecord) -> int:
        """
        创建采集任务记录（INSERT IGNORE，幂等）。

        如果同 task_type + business_date + source 的记录已存在，跳过创建。

        Args:
            task: 任务记录

        Returns:
            int: 任务 ID（已存在则返回已有 ID）
        """
        # 先查是否已存在
        existing = self._find_task(task.task_type, task.business_date, task.source)
        if existing:
            return existing['id']

        stmt = text(
            f'INSERT INTO `{TABLE_COLLECTION_TASK}` '
            '(task_type, business_date, source, status, record_count, retry_count) '
            'VALUES (:task_type, :business_date, :source, :status, :record_count, :retry_count)'
        )
        self.session.execute(stmt, {
            'task_type': task.task_type,
            'business_date': task.business_date,
            'source': task.source,
            'status': task.status,
            'record_count': task.record_count,
            'retry_count': task.retry_count,
        })
        self.session.flush()

        # 获取新插入的 ID
        result = self.session.execute(text(
            f'SELECT id FROM `{TABLE_COLLECTION_TASK}` '
            f'WHERE task_type = :tt AND business_date = :bd AND source = :src '
            f'ORDER BY id DESC LIMIT 1'
        ), {
            'tt': task.task_type,
            'bd': task.business_date,
            'src': task.source,
        })
        row = result.fetchone()
        task_id = row[0] if row else 0
        logger.debug(f"创建任务: {task.unique_key} → id={task_id}")
        return task_id

    def update_status(
        self,
        task_id: int,
        status: str,
        record_count: int = 0,
        error_message: Optional[str] = None,
    ):
        """
        更新任务状态。

        Args:
            task_id: 任务 ID
            status: 新状态 (RUNNING/SUCCESS/FAILED)
            record_count: 采集记录数
            error_message: 失败时的错误信息
        """
        now = datetime.now()
        params = {
            'id': task_id,
            'status': status,
            'record_count': record_count,
            'updated_at': now,
        }

        if status == TaskStatus.RUNNING.value:
            stmt = text(
                f'UPDATE `{TABLE_COLLECTION_TASK}` '
                f'SET status = :status, started_at = :started_at, updated_at = :updated_at '
                f'WHERE id = :id'
            )
            params['started_at'] = now
            self.session.execute(stmt, params)

        elif status == TaskStatus.SUCCESS.value:
            stmt = text(
                f'UPDATE `{TABLE_COLLECTION_TASK}` '
                f'SET status = :status, record_count = :record_count, '
                f'finished_at = :finished_at, updated_at = :updated_at '
                f'WHERE id = :id'
            )
            params['finished_at'] = now
            self.session.execute(stmt, params)

        elif status == TaskStatus.FAILED.value:
            stmt = text(
                f'UPDATE `{TABLE_COLLECTION_TASK}` '
                f'SET status = :status, record_count = :record_count, '
                f'error_message = :error_message, '
                f'finished_at = :finished_at, updated_at = :updated_at, '
                f'retry_count = retry_count + 1 '
                f'WHERE id = :id'
            )
            params['finished_at'] = now
            params['error_message'] = error_message
            self.session.execute(stmt, params)

        else:
            stmt = text(
                f'UPDATE `{TABLE_COLLECTION_TASK}` '
                f'SET status = :status, updated_at = :updated_at WHERE id = :id'
            )
            self.session.execute(stmt, params)

        logger.debug(f"更新任务 {task_id}: status={status}, count={record_count}")

    def is_task_success(self, task_type: str, business_date: str, source: str) -> bool:
        """
        判断任务是否已成功执行。

        Args:
            task_type: 任务类型
            business_date: 业务日期
            source: 数据源

        Returns:
            True 如果状态为 SUCCESS
        """
        task = self._find_task(task_type, business_date, source)
        return task is not None and task['status'] == TaskStatus.SUCCESS.value

    def get_failed_tasks(self, limit: int = 100) -> List[dict]:
        """
        查询所有失败任务。

        Args:
            limit: 最大返回数

        Returns:
            失败任务列表
        """
        result = self.session.execute(text(
            f'SELECT id, task_type, business_date, source, status, '
            f'record_count, retry_count, error_message, started_at, finished_at '
            f'FROM `{TABLE_COLLECTION_TASK}` '
            f'WHERE status = :status '
            f'ORDER BY created_at DESC LIMIT :limit'
        ), {'status': TaskStatus.FAILED.value, 'limit': limit})

        tasks = []
        for row in result:
            tasks.append({
                'id': row[0],
                'task_type': row[1],
                'business_date': row[2],
                'source': row[3],
                'status': row[4],
                'record_count': row[5],
                'retry_count': row[6],
                'error_message': row[7],
                'started_at': row[8],
                'finished_at': row[9],
            })
        return tasks

    def get_task(self, task_id: int) -> Optional[dict]:
        """根据 ID 查询任务。"""
        result = self.session.execute(text(
            f'SELECT id, task_type, business_date, source, status, '
            f'record_count, retry_count, error_message, started_at, finished_at '
            f'FROM `{TABLE_COLLECTION_TASK}` WHERE id = :id'
        ), {'id': task_id})
        row = result.fetchone()
        if row is None:
            return None
        return {
            'id': row[0],
            'task_type': row[1],
            'business_date': row[2],
            'source': row[3],
            'status': row[4],
            'record_count': row[5],
            'retry_count': row[6],
            'error_message': row[7],
            'started_at': row[8],
            'finished_at': row[9],
        }

    def _find_task(self, task_type: str, business_date: str, source: str) -> Optional[dict]:
        """按唯一键查找任务。"""
        result = self.session.execute(text(
            f'SELECT id, task_type, business_date, source, status, '
            f'record_count, retry_count, error_message '
            f'FROM `{TABLE_COLLECTION_TASK}` '
            f'WHERE task_type = :tt AND business_date = :bd AND source = :src'
        ), {'tt': task_type, 'bd': business_date, 'src': source})
        row = result.fetchone()
        if row is None:
            return None
        return {
            'id': row[0],
            'task_type': row[1],
            'business_date': row[2],
            'source': row[3],
            'status': row[4],
            'record_count': row[5],
            'retry_count': row[6],
            'error_message': row[7],
        }
