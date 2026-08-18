"""
TushareCollector 单元测试

使用 unittest.mock 模拟 Tushare 接口调用，
验证采集器返回统一格式数据。
"""

import unittest
from unittest.mock import patch, MagicMock
from datetime import date

import pandas as pd


class TestTushareCollector(unittest.TestCase):
    """TushareCollector 测试"""

    @patch('app.collectors.tushare_collector.ts')
    def test_collect_daily_returns_unified_columns(self, mock_ts):
        """数据源正常返回时字段正确"""
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        mock_ts.set_token = MagicMock()

        # 模拟 daily 返回
        mock_pro.daily.return_value = pd.DataFrame({
            'ts_code': ['000001.SZ', '000002.SZ'],
            'trade_date': ['20260703', '20260703'],
            'open': [10.0, 20.0],
            'high': [10.5, 20.5],
            'low': [9.5, 19.5],
            'close': [10.2, 20.2],
            'pre_close': [9.8, 19.8],
            'change': [0.4, 0.4],
            'pct_chg': [4.08, 2.02],
            'vol': [100000, 200000],
            'amount': [102000, 404000],
        })

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token')
        df = collector.collect_daily('20260703')

        expected_cols = [
            'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        ]
        for col in expected_cols:
            self.assertIn(col, df.columns, f"缺少列: {col}")

        self.assertEqual(len(df), 2)
        # trade_date 应为 date 类型
        self.assertIsInstance(df['trade_date'].iloc[0], date)

    @patch('app.collectors.tushare_collector.ts')
    def test_collect_daily_empty_result(self, mock_ts):
        """返回空数据时能正确识别"""
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        mock_ts.set_token = MagicMock()
        mock_pro.daily.return_value = pd.DataFrame()

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token')
        df = collector.collect_daily('20260703')

        self.assertTrue(df.empty)

    @patch('app.collectors.tushare_collector.ts')
    def test_collect_basic_returns_correct_columns(self, mock_ts):
        """获取股票基本信息字段正确"""
        mock_pro = MagicMock()
        mock_ts.pro_api.return_value = mock_pro
        mock_ts.set_token = MagicMock()

        mock_pro.stock_basic.return_value = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'symbol': ['000001'],
            'name': ['平安银行'],
            'area': ['广东'],
            'industry': ['银行'],
            'list_date': ['19910403'],
        })

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token')
        df = collector.collect_basic()

        self.assertIn('ts_code', df.columns)
        self.assertIn('name', df.columns)
        self.assertEqual(len(df), 1)

    @patch('app.collectors.tushare_collector.ts')
    def test_source_name(self, mock_ts):
        """source_name 返回正确标识"""
        mock_ts.pro_api.return_value = MagicMock()
        mock_ts.set_token = MagicMock()

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token')
        self.assertEqual(collector.source_name, 'tushare')

    def test_collect_daily_basic_returns_protocol_columns(self):
        client = MagicMock()
        client.daily_basic.return_value = pd.DataFrame({
            'ts_code': ['000001.SZ'],
            'trade_date': ['20260814'],
            'turnover_rate': [1.25],
            'pe': [6.5],
            'pe_ttm': [6.8],
            'pb': [0.72],
            'ps': [1.15],
            'total_mv': [21000000.25],
            'unused': ['ignored'],
        })

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token', client=client)
        df = collector.collect_daily_basic('2026-08-14')

        self.assertEqual(list(df.columns), [
            'ts_code', 'trade_date', 'turnover_rate',
            'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
        ])
        self.assertIsInstance(df['trade_date'].iloc[0], date)
        client.daily_basic.assert_called_once_with(trade_date='20260814')

    def test_collect_index_constituents_uses_latest_membership(self):
        client = MagicMock()
        client.index_weight.return_value = pd.DataFrame({
            'index_code': ['000300.SH', '000300.SH', '000300.SH'],
            'con_code': ['000001.SZ', '000001.SZ', '600000.SH'],
            'trade_date': ['20260801', '20260814', '20260814'],
            'weight': [0.4, 0.5, 0.6],
        })

        from app.collectors.tushare_collector import TushareCollector
        collector = TushareCollector(token='test_token', client=client)
        df = collector.collect_index_constituents('000300.SH', '20260814')

        self.assertEqual(list(df['ts_code']), ['000001.SZ', '600000.SH'])
        self.assertEqual(list(df['weight']), [0.5, 0.6])
        self.assertTrue((df['group_type'] == 'index').all())

    def test_keeps_pro_attribute_for_trade_calendar_compatibility(self):
        pro = MagicMock()
        from app.market_data.tushare_client import TushareClient
        from app.collectors.tushare_collector import TushareCollector

        collector = TushareCollector(
            token='test_token',
            client=TushareClient(pro),
        )

        self.assertIs(collector.pro, pro)


class TestToTushareCode(unittest.TestCase):
    """to_tushare_code 转换测试"""

    def test_shanghai_codes(self):
        from app.collectors.tushare_collector import to_tushare_code
        self.assertEqual(to_tushare_code('600000'), '600000.SH')
        self.assertEqual(to_tushare_code('900000'), '900000.SH')

    def test_shenzhen_codes(self):
        from app.collectors.tushare_collector import to_tushare_code
        self.assertEqual(to_tushare_code('000001'), '000001.SZ')
        self.assertEqual(to_tushare_code('300001'), '300001.SZ')

    def test_already_formatted(self):
        from app.collectors.tushare_collector import to_tushare_code
        self.assertEqual(to_tushare_code('000001.SZ'), '000001.SZ')
        self.assertEqual(to_tushare_code('600000.sh'), '600000.SH')


if __name__ == '__main__':
    unittest.main()
