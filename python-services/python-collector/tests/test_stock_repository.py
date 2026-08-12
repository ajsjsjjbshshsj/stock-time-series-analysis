"""
StockRepository 单元测试

验证行情写入、去重、读取等功能。
"""

import unittest
from unittest.mock import MagicMock, patch, call
from datetime import date

import pandas as pd
from sqlalchemy import text


class TestStockRepository(unittest.TestCase):
    """StockRepository 测试"""

    def _make_repo(self):
        from app.repositories.stock_repository import StockRepository
        session = MagicMock()
        return StockRepository(session), session

    def test_save_daily_records_empty(self):
        """空记录不执行写入"""
        repo, session = self._make_repo()
        count = repo.save_daily_records([])
        self.assertEqual(count, 0)
        session.execute.assert_not_called()

    def test_save_daily_records_batch(self):
        """批量写入调用正确次数"""
        repo, session = self._make_repo()

        records = [
            {
                'ts_code': f'{i:06d}.SZ',
                'trade_date': date(2026, 7, 3),
                'open': 10.0, 'high': 10.5, 'low': 9.5,
                'close': 10.2, 'pre_close': 9.8,
                'change': 0.4, 'pct_chg': 4.08,
                'vol': 100000, 'amount': 102000,
            }
            for i in range(10)
        ]

        count = repo.save_daily_records(records, batch_size=5)
        self.assertEqual(count, 10)
        # 应该调用 2 次（5+5）
        self.assertEqual(session.execute.call_count, 2)

    def test_save_stock_basic_empty(self):
        """空股票列表不执行写入"""
        repo, session = self._make_repo()
        count = repo.save_stock_basic([])
        self.assertEqual(count, 0)

    def test_get_latest_trade_dates(self):
        """获取最新交易日期"""
        repo, session = self._make_repo()

        mock_result = [('000001.SZ', date(2026, 7, 3)), ('000002.SZ', date(2026, 7, 2))]
        session.execute.return_value = mock_result

        result = repo.get_latest_trade_dates()

        self.assertEqual(result['000001.SZ'], date(2026, 7, 3))
        self.assertEqual(result['000002.SZ'], date(2026, 7, 2))


if __name__ == '__main__':
    unittest.main()
