"""Hand-derived fixtures catch price jumps, unit errors and mode mixing."""
import json

import numpy as np
import pandas as pd
import pytest


def fixture_panel():
    dates = pd.bdate_range('2024-01-02', periods=12)
    price = np.array([10., 10., 5., 5.] + [5.] * 8)
    market = pd.DataFrame(dict(ts_code='000001.SZ', trade_date=dates,
        open=price, high=price, low=price, close=price, vol=100., amount=price*10))
    factors = market[['ts_code', 'trade_date']].assign(adj_factor=[1., 1.] + [2.] * 10)
    return market, factors


def adapter():
    from data_processor.adjusted_market_panel import build_model_panel
    return build_model_panel


def test_adjustment_removes_mechanical_jump_without_changing_quantity():
    market, factors = fixture_panel()
    panel, contract = adapter()(market, factors)
    np.testing.assert_allclose(panel.open, 10.)
    np.testing.assert_allclose(panel.vol, 100.)
    np.testing.assert_allclose(panel.amount, market.amount)
    np.testing.assert_allclose(panel._model_vwap, 10.)
    assert contract['bases'] == {'000001.SZ': 1.}
    assert market.open.iloc[2] == 5.  # Pure adapter never mutates its input.


def test_appending_future_factor_does_not_renormalize_history():
    market, factors = fixture_panel()
    first, metadata = adapter()(market.iloc[:4], factors.iloc[:4])
    later, _ = adapter()(market, factors, bases=metadata['bases'])
    np.testing.assert_allclose(first.open, later.open.iloc[:4])
    np.testing.assert_allclose(first._model_vwap, later._model_vwap.iloc[:4])


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'zero', 'nan', 'wrong_code'])
def test_bad_factors_fail_closed(failure):
    market, factors = fixture_panel()
    if failure == 'missing':
        factors = factors.iloc[:-1]
    elif failure == 'duplicate':
        factors = pd.concat([factors, factors.iloc[:1]])
    elif failure == 'wrong_code':
        factors = factors.assign(ts_code='000002.SZ')
    else:
        factors.loc[0, 'adj_factor'] = 0 if failure == 'zero' else np.nan
    with pytest.raises(ValueError, match='factor|Factor'):
        adapter()(market, factors)


def test_control_panel_has_correct_vwap_units_but_raw_prices():
    market, factors = fixture_panel()
    panel, contract = adapter()(market, factors, mode='unadjusted_control')
    np.testing.assert_allclose(panel.open, market.open)
    np.testing.assert_allclose(panel._model_vwap, market.open)
    assert contract['mode'] == 'unadjusted_control'


def test_zero_volume_is_finite():
    market, factors = fixture_panel()
    market.loc[0, 'vol'] = 0
    panel, _ = adapter()(market, factors)
    assert panel._model_vwap.iloc[0] == 0 and np.isfinite(panel._model_vwap).all()


def test_adjusted_label_and_vwap_feature_use_same_prices():
    from analysis.transformer_features import build_feature_panel
    from analysis.transformer_config import TRANSFORMER_CONFIG
    market, factors = fixture_panel()
    panel, contract = adapter()(market, factors)
    config = dict(TRANSFORMER_CONFIG, market_preprocessing=contract)
    raw, _, _, _ = build_feature_panel(panel, config)
    np.testing.assert_allclose(raw.VWAP0, 1., atol=1e-6)
    assert raw.label.iloc[0] == pytest.approx(0.)


def test_adjusted_panel_cannot_use_legacy_configuration():
    from analysis.transformer_features import build_feature_panel
    from analysis.transformer_config import TRANSFORMER_CONFIG
    market, factors = fixture_panel()
    panel, _ = adapter()(market, factors)
    with pytest.raises(ValueError, match='market|price|contract'):
        build_feature_panel(panel, TRANSFORMER_CONFIG)


def test_cache_contract_mismatch_rejected(tmp_path):
    from analysis.transformer_features import save_feature_cache, load_feature_cache
    from analysis.transformer_config import TRANSFORMER_CONFIG
    market, factors = fixture_panel()
    panel, contract = adapter()(market, factors)
    config = dict(TRANSFORMER_CONFIG, market_preprocessing=contract)
    path = tmp_path/'features.parquet'
    save_feature_cache(panel, path, config, use_parallel=False)
    load_feature_cache(path, config)
    with pytest.raises(ValueError, match='market|price|contract'):
        load_feature_cache(path, TRANSFORMER_CONFIG)


def test_model_contract_mismatch_rejected(tmp_path):
    from sklearn.preprocessing import StandardScaler
    from analysis.transformer_features import save_model_preprocessing, load_model_preprocessing, feature_columns
    from analysis.transformer_config import TRANSFORMER_CONFIG
    _, contract = adapter()(*fixture_panel())
    config = dict(TRANSFORMER_CONFIG, feature_num='39', market_preprocessing=contract)
    columns = feature_columns('39')
    scaler = StandardScaler().fit(pd.DataFrame(np.zeros((2,len(columns))), columns=columns))
    path = tmp_path/'model.pth'
    path.write_bytes(b'checkpoint-only-fixture')
    save_model_preprocessing(path, scaler, columns, columns, {'000001.SZ':0}, config,
        '2024-01-02', {'000001.SZ':'2024-01-02'})
    load_model_preprocessing(path, config)
    with pytest.raises(ValueError, match='market|price|contract'):
        load_model_preprocessing(path, dict(config, market_preprocessing={'mode':'legacy_unadjusted'}))
