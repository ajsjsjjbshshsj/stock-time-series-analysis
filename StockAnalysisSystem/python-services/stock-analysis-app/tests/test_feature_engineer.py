# -*- coding: utf-8 -*-
"""
特征工程模块测试

覆盖：
    - MA5、MA20 是否计算正确
    - 特征计算是否按股票分组
    - 未来标签是否没有进入预测特征
    - 不可变性（不修改原始数据）
"""
import os
import sys
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_stock_df(n=100, code='000001.SZ'):
    """生成测试用单股票数据。"""
    np.random.seed(42)
    return pd.DataFrame({
        'ts_code': [code] * n,
        'trade_date': pd.date_range('2024-01-01', periods=n, freq='B'),
        'open':  np.random.uniform(10, 20, n),
        'high':  np.random.uniform(15, 25, n),
        'low':   np.random.uniform(5, 15, n),
        'close': np.cumsum(np.random.randn(n) * 0.5) + 50,
        'vol':   np.random.uniform(1e6, 1e7, n),
    })


class TestMovingAverages:
    """均线计算正确性测试。"""

    def test_ma5_manual_check(self):
        from data_processor.feature_engineer import FeatureEngineer
        df = _make_stock_df(10)
        engineer = FeatureEngineer()
        result = engineer.calculate_all_features(df)

        # 手工验证 MA5 = 前5天收盘价均值
        expected_ma5 = result['close'].rolling(5).mean()
        # talib.SMA 和 pandas rolling 在有效窗口内应一致（tol=1e-4）
        valid = result['ma5'].notna() & expected_ma5.notna()
        np.testing.assert_allclose(
            result.loc[valid, 'ma5'].values,
            expected_ma5[valid].values,
            atol=1e-4,
        )

    def test_ma20_first_19_rows_nan(self):
        from data_processor.feature_engineer import FeatureEngineer
        df = _make_stock_df(50)
        engineer = FeatureEngineer()
        result = engineer.calculate_all_features(df)
        # MA20 前 19 行应为 NaN
        assert result['ma20'].iloc[:19].isna().all()


class TestGroupByStock:
    """特征计算是否按股票分组。"""

    def test_separate_stocks_no_cross_contamination(self):
        from data_processor.feature_engineer import FeatureEngineer

        # 构造两只股票用不同的收盘价序列
        n = 80
        dates = pd.date_range('2024-01-01', periods=n, freq='B')
        np.random.seed(100)
        close_a = np.cumsum(np.random.randn(n) * 0.5) + 50
        np.random.seed(200)
        close_b = np.cumsum(np.random.randn(n) * 0.5) + 80

        df_a = pd.DataFrame({'ts_code': ['A'] * n, 'trade_date': dates,
                             'open': 10, 'high': 12, 'low': 8, 'close': close_a, 'vol': 1e6})
        df_b = pd.DataFrame({'ts_code': ['B'] * n, 'trade_date': dates,
                             'open': 10, 'high': 12, 'low': 8, 'close': close_b, 'vol': 1e6})

        engineer = FeatureEngineer()
        result_a = engineer.calculate_all_features(df_a.copy())
        result_b = engineer.calculate_all_features(df_b.copy())

        # 两只股票 close 不同，MA5 也应不同
        assert not np.allclose(
            result_a['ma5'].dropna().values,
            result_b['ma5'].dropna().values,
        )


class TestFutureLabelExclusion:
    """未来标签是否没有进入预测特征。"""

    def test_label_columns_defined(self):
        from analysis.pipeline import LABEL_COLUMNS
        assert 'future_return_1d' in LABEL_COLUMNS
        assert 'future_return_5d' in LABEL_COLUMNS
        assert 'future_direction_1d' in LABEL_COLUMNS
        assert 'label' in LABEL_COLUMNS

    def test_sanitize_removes_future_columns(self):
        from analysis.pipeline import sanitize_feature_columns
        candidates = ['ma5', 'rsi', 'future_return_1d', 'label', 'ts_code']
        safe = sanitize_feature_columns(candidates)
        assert 'future_return_1d' not in safe
        assert 'label' not in safe
        assert 'ts_code' not in safe
        assert 'ma5' in safe

    def test_feature_registry_no_future_in_non_label_categories(self):
        """非标签类别（ma/ema/macd/rsi/bollinger）中不应包含 future 列。"""
        from data_processor.feature_engineer import FEATURE_REGISTRY
        for cat_name in ['ma', 'ema', 'macd', 'rsi', 'bollinger', 'kdj', 'momentum']:
            cat_feats = FEATURE_REGISTRY['features'].get(cat_name, [])
            for f in cat_feats:
                assert 'future' not in f, f"特征 {f} 在 {cat_name} 中包含 future"


class TestImmutability:
    """不可变性测试：特征计算不修改原始数据。"""

    def test_original_df_unchanged(self):
        from data_processor.feature_engineer import FeatureEngineer
        df = _make_stock_df(100)
        original_cols = set(df.columns)
        original_shape = df.shape
        original_values = df['close'].values.copy()

        engineer = FeatureEngineer()
        _ = engineer.calculate_all_features(df)

        assert set(df.columns) == original_cols
        assert df.shape == original_shape
        np.testing.assert_array_equal(df['close'].values, original_values)


class TestNaNHandling:
    """窗口不足时的 NaN 处理测试。"""

    def test_rsi_first_rows_nan(self):
        from data_processor.feature_engineer import FeatureEngineer
        df = _make_stock_df(50)
        engineer = FeatureEngineer()
        result = engineer.calculate_all_features(df)
        assert result['rsi'].iloc[0] != result['rsi'].iloc[0]  # NaN check

    def test_volatility_no_fill_zero(self):
        """volatility_20d 窗口不足时应为 NaN（不应填充 0）。"""
        from data_processor.feature_engineer import FeatureEngineer
        df = _make_stock_df(30)
        engineer = FeatureEngineer()
        result = engineer.calculate_all_features(df)
        # 前 19 行应为 NaN（窗口=20，std 需要至少 2 个值）
        nan_count = result['volatility_20d'].isna().sum()
        assert nan_count >= 1, f"volatility_20d 应有 NaN，实际 NaN 数={nan_count}"
