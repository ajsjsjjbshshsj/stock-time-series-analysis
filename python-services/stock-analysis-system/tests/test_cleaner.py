# -*- coding: utf-8 -*-
"""
数据清洗模块测试

覆盖：
    - 重复行情能否被去除
    - 非法价格能否被识别
    - 日期是否正确转换
"""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDuplicateRemoval:
    """重复行情去除测试。"""

    def test_drop_duplicates_keeps_last(self):
        df = pd.DataFrame({
            'ts_code': ['A', 'A', 'A'],
            'trade_date': ['2024-01-01', '2024-01-01', '2024-01-02'],
            'close': [10.0, 11.0, 12.0],
        })
        result = df.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
        assert len(result) == 2
        assert result.iloc[0]['close'] == 11.0  # 保留最后一条

    def test_no_duplicates_unchanged(self):
        df = pd.DataFrame({
            'ts_code': ['A', 'B'],
            'trade_date': ['2024-01-01', '2024-01-01'],
            'close': [10.0, 20.0],
        })
        result = df.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
        assert len(result) == 2


class TestInvalidPrice:
    """非法价格识别测试。"""

    def test_detect_negative_price(self):
        df = pd.DataFrame({'close': [10.0, -5.0, 20.0]})
        invalid_mask = df['close'] <= 0
        assert invalid_mask.sum() == 1

    def test_detect_zero_volume(self):
        df = pd.DataFrame({'vol': [1e6, 0, 5e5]})
        zero_mask = df['vol'] == 0
        assert zero_mask.sum() == 1

    def test_detect_inf_values(self):
        df = pd.DataFrame({'close': [10.0, np.inf, -np.inf, 20.0]})
        cleaned = df.replace([np.inf, -np.inf], np.nan)
        assert cleaned['close'].isna().sum() == 2


class TestDateConversion:
    """日期转换测试。"""

    def test_string_to_datetime(self):
        df = pd.DataFrame({'trade_date': ['2024-01-01', '2024-06-15']})
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        assert 'datetime64' in str(df['trade_date'].dtype)

    def test_int_to_datetime(self):
        df = pd.DataFrame({'trade_date': [20240101, 20240615]})
        df['trade_date'] = pd.to_datetime(df['trade_date'].astype(str), format='%Y%m%d')
        assert df['trade_date'].iloc[0] == pd.Timestamp('2024-01-01')

    def test_sort_by_date(self):
        df = pd.DataFrame({
            'ts_code': ['A', 'A', 'A'],
            'trade_date': ['2024-01-03', '2024-01-01', '2024-01-02'],
            'close': [12, 10, 11],
        })
        df['trade_date'] = pd.to_datetime(df['trade_date'])
        df = df.sort_values('trade_date').reset_index(drop=True)
        assert df.iloc[0]['close'] == 10
        assert df.iloc[2]['close'] == 12
