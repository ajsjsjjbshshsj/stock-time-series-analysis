"""Training boundary and real tiny-model inference regression tests."""
import hashlib

import numpy as np
import pandas as pd
import pytest
import torch
from sklearn.preprocessing import StandardScaler

from analysis import transformer_features as pipeline
from analysis import transformer_trainer as trainer
from analysis.transformer_model import StockTransformer
from tests.test_transformer_features import market_panel, configuration


def test_training_scaler_uses_only_mature_pre_validation_labels(tmp_path):
    raw, features, _, split = pipeline.build_feature_panel(market_panel(), configuration(tmp_path))
    boundary = pd.Timestamp(split)
    raw.loc[raw['日期'] >= boundary, features] *= 10000
    eligible = (raw['日期'] < boundary) & (raw.label_target_date < boundary) & raw.label.notna()
    train, val, context, scaler = pipeline.prepare_training_data(raw, features, split)
    np.testing.assert_allclose(scaler.mean_, raw.loc[eligible, features].mean())
    assert train.label.notna().all() and (train.label_target_date < boundary).all()
    assert (val['日期'] >= boundary).all() and val.label.notna().all()
    purged = (context['日期'] < boundary) & (context.label_target_date >= boundary)
    assert purged.any() and context.loc[purged, 'label'].isna().all()
    assert len(context) == len(raw)
    np.testing.assert_allclose(context[features], scaler.transform(raw[features]))
    assert list(scaler.feature_names_in_) == features


def test_preprocess_inference_keeps_latest_unlabeled_date(tmp_path):
    panel = market_panel()
    actual, _ = trainer.preprocess_data(panel, is_train=False, config=configuration(tmp_path))
    assert actual['日期'].max() == panel.trade_date.max()
    assert len(actual) == len(panel)


def tiny_bundle(tmp_path, panel, feature_num='39'):
    config = dict(configuration(tmp_path), feature_num=feature_num, sequence_length=10, d_model=8, nhead=2,
                  num_layers=1, dim_feedforward=16, dropout=0.0, use_multi_head=False)
    raw, features, mapping, split = pipeline.build_feature_panel(panel, config)
    _, _, _, scaler = pipeline.prepare_training_data(raw, features, split)
    model = StockTransformer(len(features), config, len(mapping))
    path = tmp_path / 'tiny.pth'
    torch.save(model.state_dict(), path)
    pipeline.save_model_preprocessing(path, scaler, features, features, mapping, config, raw['日期'].min(),
                                     raw.groupby('股票代码')['日期'].min().to_dict())
    return config, raw, features, scaler, path


@pytest.mark.parametrize('cached', [False, True])
@pytest.mark.parametrize('stale', [False, True])
def test_prediction_uses_latest_features_and_frozen_model_scaler(tmp_path, monkeypatch, cached, stale):
    panel = market_panel(count=320)
    config, raw, features, scaler, model_path = tiny_bundle(tmp_path, panel)
    if stale:
        panel = panel[~((panel.ts_code == '000002.SZ') & (panel.trade_date == panel.trade_date.max()))]
    expected, _, _, _ = pipeline.build_feature_panel(panel, config)
    expected.loc[:, features] = scaler.transform(expected[features])
    latest = expected['日期'].max()
    expected_sequences = [g.tail(10)[features].to_numpy(dtype=np.float32)
                          for _, g in expected.groupby('股票代码') if g['日期'].max() == latest]
    observed = []
    original = StockTransformer.forward
    def capture(self, x, *args, **kwargs):
        observed.append(x.detach().cpu().numpy())
        return original(self, x, *args, **kwargs)
    monkeypatch.setattr(StockTransformer, 'forward', capture)
    def forbidden_fit(*args, **kwargs):
        raise AssertionError('Inference must never fit a scaler')
    monkeypatch.setattr(StandardScaler, 'fit', forbidden_fit)
    # Plotting is an external side effect, not the inference logic under test.
    monkeypatch.setattr('visualization.plotter.StockPlotter', lambda: object())
    arguments = dict(panel_df=panel)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(panel, cache, config, use_parallel=False)
        before = hashlib.sha256(cache.read_bytes()).hexdigest()
        arguments = dict(feature_path=str(cache))
    result = trainer.predict_top_stocks_transformer(model_path=str(model_path), config=config, **arguments)
    assert set(result['股票代码']) == ({'000001.SZ'} if stale else {'000001.SZ', '000002.SZ'})
    np.testing.assert_allclose(observed[0][0], np.stack(expected_sequences), rtol=1e-6)
    if cached:
        assert hashlib.sha256(cache.read_bytes()).hexdigest() == before


def test_model_bundle_rejects_missing_or_mismatched_scaler(tmp_path):
    config, _, features, _, model_path = tiny_bundle(tmp_path, market_panel(count=320))
    scaler_path = model_path.with_name('tiny_scaler.pkl')
    scaler_path.unlink()
    with pytest.raises(ValueError, match='scaler|retrain'):
        pipeline.load_model_preprocessing(model_path, config)


def test_model_bundle_rejects_wrong_configuration_and_checkpoint(tmp_path):
    config, _, _, _, model_path = tiny_bundle(tmp_path, market_panel(count=320))
    with pytest.raises(ValueError, match='config|configuration'):
        pipeline.load_model_preprocessing(model_path, dict(config, sequence_length=20))
    with model_path.open('ab') as stream:
        stream.write(b'changed')
    with pytest.raises(ValueError, match='model|checkpoint'):
        pipeline.load_model_preprocessing(model_path, config)


def test_fixed_history_anchor_does_not_slide_obv_origin(tmp_path):
    panel = market_panel(count=850)
    config = configuration(tmp_path)
    initial = panel.groupby('ts_code').head(800)
    old, _, _, _ = pipeline.build_feature_panel(initial, config)
    anchored, _, _, _ = pipeline.build_feature_panel(panel, dict(config, feature_start_date=str(old['日期'].min())))
    code = '000001.SZ'
    history = panel[(panel.ts_code == code) & (panel.trade_date >= old['日期'].min())]
    import talib
    np.testing.assert_allclose(anchored[anchored['股票代码'] == code].obv,
                               talib.OBV(history.close.to_numpy(), history.vol.to_numpy()))


@pytest.mark.parametrize('cached', [False, True])
def test_training_entrances_share_purged_split_and_validation_context(tmp_path, monkeypatch, cached):
    panel = market_panel(count=320, stocks=10)
    config = dict(configuration(tmp_path), use_probe_selection=False)
    raw, features, _, split = pipeline.build_feature_panel(panel, config)
    train, _, context, _ = pipeline.prepare_training_data(raw, features, split)
    captured = []
    class StopBeforeTraining(Exception):
        pass
    def dataset(frame, columns, length, **kwargs):
        captured.append(frame.copy())
        if len(captured) == 2:
            raise StopBeforeTraining
        return ([], [], [], [], [], [])
    monkeypatch.setattr(trainer, 'create_ranking_dataset_vectorized', dataset)
    args = dict(panel_df=panel)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(panel, cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    with pytest.raises(StopBeforeTraining):
        trainer.run_transformer_training(config=config, **args)
    pd.testing.assert_frame_equal(captured[0], context[context['日期'] < pd.Timestamp(split)])
    pd.testing.assert_frame_equal(captured[1], context)


def test_inference_rejects_wrong_scaler_column_order(tmp_path):
    raw, features, _, _ = pipeline.build_feature_panel(market_panel(count=320), configuration(tmp_path))
    wrong = StandardScaler().fit(raw[list(reversed(features))])
    with pytest.raises(ValueError, match='order'):
        pipeline.prepare_inference_data(raw, features, wrong)


@pytest.mark.parametrize('cached', [False, True])
def test_synthetic_one_epoch_training_emits_a_usable_bound_model(tmp_path, monkeypatch, cached):
    panel = market_panel(count=100, stocks=10)
    config = dict(configuration(tmp_path), sequence_length=10, d_model=8, nhead=2,
                  num_layers=1, dim_feedforward=16, dropout=0.0, use_multi_head=False,
                  use_probe_selection=False, num_epochs=1, batch_size=128)
    monkeypatch.setattr('visualization.plotter.StockPlotter', lambda: object())
    args = dict(panel_df=panel)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(panel, cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    previous_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        result = trainer.run_transformer_training(config=config, use_multi_head=False, **args)
        scaler, manifest = pipeline.load_model_preprocessing(result['model_path'], result['config'])
        assert result['best_epoch'] == 1
        assert manifest['selected_features'] == result['feature_names']
        forecast = trainer.predict_top_stocks_transformer(model_path=result['model_path'],
                                                         config=result['config'], panel_df=panel)
        assert len(forecast) == 5 and np.isfinite(forecast['预测分数']).all()
    finally:
        torch.set_num_threads(previous_threads)


@pytest.mark.parametrize('cached', [False, True])
def test_each_stock_requires_its_own_complete_model_history(tmp_path, monkeypatch, cached):
    panel = market_panel(count=320)
    config, _, _, _, model_path = tiny_bundle(tmp_path, panel, feature_num='158+39')
    truncated = panel[~((panel.ts_code == '000002.SZ') & (panel.trade_date < panel.trade_date.unique()[200]))]
    monkeypatch.setattr('visualization.plotter.StockPlotter', lambda: object())
    args = dict(panel_df=truncated)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(truncated, cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    with pytest.raises(ValueError, match='history|历史'):
        trainer.predict_top_stocks_transformer(model_path=str(model_path), config=config, **args)


@pytest.mark.parametrize('cached', [False, True])
def test_unknown_stock_latest_date_does_not_make_old_known_windows_current(tmp_path, monkeypatch, cached):
    panel = market_panel(count=320)
    config, _, _, _, model_path = tiny_bundle(tmp_path, panel)
    new = market_panel(count=321, stocks=3).query("ts_code == '000003.SZ'").tail(1)
    monkeypatch.setattr('visualization.plotter.StockPlotter', lambda: object())
    args = dict(panel_df=pd.concat([panel, new]))
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(args['panel_df'], cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    result = trainer.predict_top_stocks_transformer(model_path=str(model_path), config=config, **args)
    assert result is None


@pytest.mark.parametrize('cached', [False, True])
def test_training_keeps_feature_sessions_with_invalid_labels(tmp_path, monkeypatch, cached):
    panel = market_panel(count=320, stocks=10)
    bad_date = panel.trade_date.unique()[100]
    panel.loc[panel.trade_date == bad_date, 'open'] = 0.0
    config = dict(configuration(tmp_path), use_probe_selection=False)
    raw, features, _, split = pipeline.build_feature_panel(panel, config)
    _, _, context, _ = pipeline.prepare_training_data(raw, features, split)
    expected = context[context['日期'] < pd.Timestamp(split)]
    captured = []
    class StopBeforeTraining(Exception):
        pass
    def capture(frame, columns, length, **kwargs):
        captured.append(frame)
        raise StopBeforeTraining
    monkeypatch.setattr(trainer, 'create_ranking_dataset_vectorized', capture)
    args = dict(panel_df=panel)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(panel, cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    with pytest.raises(StopBeforeTraining):
        trainer.run_transformer_training(config=config, **args)
    pd.testing.assert_frame_equal(captured[0], expected)
    assert captured[0].label.isna().any()


@pytest.mark.parametrize('cached', [False, True])
def test_legitimate_later_listing_uses_its_own_history_start(tmp_path, monkeypatch, cached):
    panel = market_panel(count=320)
    panel = panel[~((panel.ts_code == '000002.SZ') & (panel.trade_date < panel.trade_date.unique()[60]))]
    config, _, _, _, model_path = tiny_bundle(tmp_path, panel)
    _, manifest = pipeline.load_model_preprocessing(model_path, config)
    assert manifest['stock_history_starts']['000001.SZ'] != manifest['stock_history_starts']['000002.SZ']
    monkeypatch.setattr('visualization.plotter.StockPlotter', lambda: object())
    args = dict(panel_df=panel)
    if cached:
        cache = tmp_path / 'features.parquet'
        pipeline.save_feature_cache(panel, cache, config, use_parallel=False)
        args = dict(feature_path=str(cache))
    result = trainer.predict_top_stocks_transformer(model_path=str(model_path), config=config, **args)
    assert set(result['股票代码']) == {'000001.SZ', '000002.SZ'}
