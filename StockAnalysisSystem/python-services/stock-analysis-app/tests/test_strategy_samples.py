import importlib
import numpy as np
import pandas as pd
import pytest


def module():
    try:
        return importlib.import_module('analysis.strategy_samples')
    except ModuleNotFoundError:
        pytest.fail('dated strategy samples not implemented')


def panel(n=60):
    return pd.DataFrame([dict(ts_code=code, trade_date=d, close=10+i%3,
                             feature=float(i)) for code in ['000001.SZ', '600000.SH']
                         for i, d in enumerate(pd.bdate_range('2025-01-01', periods=n))])


@pytest.mark.parametrize('source', ['engineer', 'pipeline'])
def test_unknown_direction_labels_are_not_false_down(source):
    data = panel(12)
    if source == 'pipeline':
        from analysis.pipeline import compute_labels
        result = compute_labels(data)
    else:
        from data_processor.feature_engineer import FeatureEngineer
        result = pd.concat([FeatureEngineer()._calc_returns(g.copy())
                            for _, g in data.groupby('ts_code')])
    assert result.groupby('ts_code').tail(1).future_direction_1d.isna().all()
    assert result.groupby('ts_code').tail(5).future_direction_5d.isna().all()


def test_dated_sample_alignment_survives_internal_feature_gaps():
    data = panel(12)
    data.loc[3, 'feature'] = np.nan
    result = module().dated_samples(data, ['feature'], horizon=1)
    row = result[(result.ts_code == '000001.SZ') & (result.trade_date == pd.Timestamp('2025-01-07'))].iloc[0]
    assert row.label_target_date == pd.Timestamp('2025-01-08')
    assert row.label == 1
    assert len(result) == 21  # one internal missing feature and two unknown labels
    assert result.loc[result.ts_code == '000001.SZ', 'trade_date'].min() == pd.Timestamp('2025-01-01')


def test_whole_date_splits_purge_labels_at_boundary():
    samples = module().dated_samples(panel(), ['feature'], horizon=5, classification=False)
    split = module().purged_splits(samples)
    assert set(split['train'].trade_date).isdisjoint(split['val'].trade_date)
    assert set(split['val'].trade_date).isdisjoint(split['test'].trade_date)
    assert split['train'].label_target_date.max() < split['val'].trade_date.min()
    assert split['val'].label_target_date.max() < split['test'].trade_date.min()
    for group in ['train', 'val', 'test']:
        assert split[group].groupby('trade_date').size().eq(2).all()
    assert split['seen_through'] < split['test'].trade_date.min().strftime('%Y-%m-%d')


@pytest.mark.parametrize('features', [['label'], ['future_return_1d'], ['label_target_date'], ['missing']])
def test_unsafe_or_missing_features_rejected(features):
    with pytest.raises(ValueError):
        module().dated_samples(panel(), features)


def test_panel_keep_unlabelled_retains_latest_and_default_filters(tmp_path):
    from data_processor.panel_builder import prepare_panel_for_training
    data = panel(150).assign(open=10., high=13., low=9., vol=100., amount=1000.)
    try:
        live = prepare_panel_for_training(data, use_parallel=False, keep_unlabelled=True,
                                          cache_dir=tmp_path / 'live')
    except TypeError as exc:
        pytest.fail(str(exc))
    train = prepare_panel_for_training(data, use_parallel=False, cache_dir=tmp_path / 'train')
    assert live.trade_date.max() == data.trade_date.max()
    assert live.groupby('ts_code').tail(5).label.isna().all()
    assert train.label.notna().all()
    assert 'label_target_date' in train


def test_label_target_date_follows_each_stock_history():
    from data_processor.panel_builder import add_label
    result = add_label(panel(12), forward_days=5)
    assert 'label_target_date' in result
    assert result.iloc[0].label_target_date == pd.Timestamp('2025-01-08')
    assert result.groupby('ts_code').tail(5).label_target_date.isna().all()
