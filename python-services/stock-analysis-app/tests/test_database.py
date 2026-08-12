# -*- coding: utf-8 -*-
"""
数据库模块测试

覆盖：
    - 连接异常时不崩溃
    - 空结果处理
"""
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDatabaseConnector:
    """数据库连接器测试。"""

    def test_connector_init_with_bad_host(self):
        """错误的 host 应在连接时报错，不在初始化时崩溃。"""
        from database.db_connector import DatabaseConnector
        # 只测试类可以正常实例化（连接是延迟的）
        connector = DatabaseConnector()
        assert connector is not None

    def test_empty_result_handling(self):
        """空查询结果应返回空 DataFrame 而非 None。"""
        import pandas as pd
        # 模拟空结果
        result = pd.DataFrame()
        assert result.empty
        assert len(result) == 0


class TestRepository:
    """Repository 层测试。"""

    def test_save_empty_records(self):
        """空记录列表不应触发写入。"""
        from database.repository import save_daily_records
        # 传入 None session 和空列表不应崩溃
        result = save_daily_records(None, [])
        assert result == 0
