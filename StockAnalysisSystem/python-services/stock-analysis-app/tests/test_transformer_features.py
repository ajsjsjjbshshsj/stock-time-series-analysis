"""Regression tests for real TA-Lib features and raw Parquet cache boundaries."""
import json

import numpy as np
import pandas as pd
import pytest
import talib
from pandas.testing import assert_frame_equal

from analysis import transformer_trainer as trainer


def market_panel(count=450, stocks=2):
    dates = pd.bdate_range('2024-01-02', periods=count)
    parts = []
    for code in range(stocks):
        t = np.arange(count)
        close = 30 + code * 2 + t * .01 + np.sin(t * .27 + code)
        parts.append(pd.DataFrame(dict(ts_code=f'{code+1:06d}.SZ', trade_date=dates,
            open=close * .998, high=close * 1.01, low=close * .99, close=close,
            vol=1e6 + code * 10000 + t * 100, amount=close * 1e6)))
    return pd.concat(parts, ignore_index=True)


def configuration(tmp_path):
    return dict(trainer.TRANSFORMER_CONFIG, feature_num='39', output_dir=str(tmp_path))


def compute(frame, path, config):
    result = trainer.compute_and_save_features(frame, str(path), config, use_parallel=False)
    assert isinstance(result, tuple) and len(result) == 3
    return pd.read_parquet(path)


def test_cache_keeps_latest_rows_without_future_labels(tmp_path):
    panel = market_panel()
    path = tmp_path / 'features.parquet'
    cached = compute(panel, path, configuration(tmp_path))
    assert len(cached) == 900
    assert pd.Timestamp(cached['日期'].max()) == panel.trade_date.max()
    assert cached.groupby('股票代码').label.apply(lambda x: x.tail(5).isna().all()).all()
    assert cached.is_val.dtype == bool and not cached.is_val.isna().any()
    assert not path.with_name('features_scaler.pkl').exists()


def test_cache_features_are_raw_and_path_dependent_history_is_not_reset(tmp_path):
    panel = market_panel(stocks=1)
    path = tmp_path / 'features.parquet'
    cached = compute(panel, path, configuration(tmp_path)).sort_values('日期')
    close = panel.close.to_numpy()
    volume = panel.vol.to_numpy()
    expected = {
        'sma_5': np.nan_to_num(talib.SMA(close, timeperiod=5)),
        'obv': talib.OBV(close, volume),
        'ema_60': np.nan_to_num(talib.EMA(close, timeperiod=60)),
        'daily_standard_deviation': panel.close.pct_change().ewm(halflife=42, min_periods=252).std().fillna(0).to_numpy(),
    }
    for field, values in expected.items():
        np.testing.assert_allclose(cached[field], values, rtol=1e-12, atol=1e-12)
    assert cached.loc[cached.is_val, 'daily_standard_deviation'].gt(0).all()
    meta = json.loads(path.with_name('features_meta.json').read_text(encoding='utf-8'))
    assert meta['cache_version'] == 2 and meta['feature_scale'] == 'raw'


@pytest.mark.parametrize('mode', ['append', 'repeat', 'correction', 'new_stock', 'unsorted'])
def test_incremental_merge_equals_full_raw_rebuild(tmp_path, mode):
    config = configuration(tmp_path)
    panel = market_panel()
    initial = panel.groupby('ts_code', group_keys=False).head(430).copy()
    incoming = panel.copy()
    if mode == 'repeat':
        initial = panel.copy()
    elif mode == 'correction':
        incoming = panel[panel.trade_date == panel.trade_date.iloc[100]].copy()
        incoming.loc[:, ['open', 'high', 'low', 'close']] *= 1.05
    elif mode == 'new_stock':
        incoming = market_panel(stocks=3).query("ts_code == '000003.SZ'").iloc[:200]
    elif mode == 'unsorted':
        incoming = panel.sample(frac=1, random_state=42)
    path = tmp_path / 'incremental.parquet'
    compute(initial, path, config)
    result = trainer.compute_and_save_features_incremental(incoming, str(path), config, use_parallel=False)
    assert isinstance(result, tuple) and len(result) == 3
    actual = pd.read_parquet(path)
    merged = pd.concat([initial, incoming]).drop_duplicates(['ts_code', 'trade_date'], keep='last')
    expected = compute(merged, tmp_path / 'full.parquet', config)
    assert not actual.duplicated(['股票代码', '日期']).any()
    assert actual.is_val.dtype == bool and not actual.is_val.isna().any()
    assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True))


def test_input_sorting_happens_before_previous_close_features(tmp_path):
    panel = market_panel()
    config = configuration(tmp_path)
    sorted_result = compute(panel, tmp_path / 'sorted.parquet', config)
    shuffled_result = compute(panel.sample(frac=1, random_state=7), tmp_path / 'shuffled.parquet', config)
    assert_frame_equal(sorted_result, shuffled_result)


def test_legacy_or_mismatched_metadata_cannot_be_silently_loaded(tmp_path):
    path = tmp_path / 'features.parquet'
    config = configuration(tmp_path)
    compute(market_panel(), path, config)
    meta_path = path.with_name('features_meta.json')
    original = json.loads(meta_path.read_text(encoding='utf-8'))
    for changes in ({'cache_version': 1}, {'feature_scale': 'standardized'}, {'feature_cols': ['sma_5']}):
        meta_path.write_text(json.dumps(dict(original, **changes)), encoding='utf-8')
        with pytest.raises(ValueError, match='cache|缓存|rebuild|重建'):
            trainer.load_precomputed_features(str(path), config)


def test_raw_stock_merge_recomputes_the_complete_date_cross_section(tmp_path):
    config = configuration(tmp_path)
    initial = market_panel(count=320, stocks=10)
    incoming = market_panel(count=320, stocks=11).query("ts_code == '000011.SZ'")
    path = tmp_path / 'features.parquet'
    compute(initial, path, config)
    trainer.compute_and_save_features_incremental(incoming, str(path), config, use_parallel=False)
    cached = pd.read_parquet(path)
    latest = cached[cached['日期'] == cached['日期'].max()]
    assert len(latest) == 11
    assert latest.cs_price_pct.nunique() == 11
    np.testing.assert_allclose(sorted(latest.cs_price_pct), np.arange(1, 12) / 11, rtol=1e-6)
