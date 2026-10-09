import numpy as np
import pandas as pd
import pytest

from analysis import ranking_predictor as ranking


def panel():
    return pd.DataFrame([dict(ts_code=c, trade_date=d, close=10.+i%5, feature=float(i%5))
                         for c in ['000001.SZ', '600000.SH']
                         for i, d in enumerate(pd.bdate_range('2024-01-01', periods=100))])


def train(data, monkeypatch):
    from config.settings import RANKING_CONFIG
    monkeypatch.setitem(RANKING_CONFIG, 'lgb_params', dict(objective='regression', metric='rmse',
                        n_estimators=5, num_leaves=3, min_data_in_leaf=2, num_threads=1, verbosity=-1))
    try:
        return ranking.train_ranking_model(data, ['feature'], use_probe=False, save_model=False)
    except TypeError as exc:
        pytest.fail(str(exc))


def test_training_purges_whole_dates_and_records_seen_evidence(monkeypatch):
    result = train(panel(), monkeypatch)
    proof = result['metadata']
    splits = proof['splits']
    assert proof['validation_schema'] == 1
    assert splits['train']['label_end'] < splits['val']['signal_start']
    assert splits['val']['label_end'] < splits['test']['signal_start']
    assert proof['seen_through'] < splits['test']['signal_start']
    assert result['model_path'] is None


def test_test_prices_cannot_influence_training_or_selection(monkeypatch):
    data = panel()
    first = train(data, monkeypatch)
    modified = data.copy()
    modified.loc[modified.trade_date >= pd.Timestamp(first['metadata']['splits']['test']['signal_start']), 'close'] *= 1.2
    second = train(modified, monkeypatch)
    np.testing.assert_array_equal(first['model'].predict([[1.], [2.]]), second['model'].predict([[1.], [2.]]))


class Scores:
    def predict(self, x): return np.ones(len(x))


def test_latest_unlabelled_ranking_is_stable_and_uses_actual_model_features(monkeypatch):
    monkeypatch.setattr(ranking, 'load_ranking_model', lambda p=None: (Scores(), ['feature'], {}))
    data = panel().sort_values('ts_code', ascending=False)
    data['label'] = np.nan
    result = ranking.predict_top_n(data, top_n=2)
    assert list(result.ts_code) == ['000001.SZ', '600000.SH']
    assert result.trade_date.max() == data.trade_date.max()
    assert list(result['排名']) == [1, 2]


@pytest.mark.parametrize('kind', ['missing', 'nan', 'infinite_score'])
def test_latest_invalid_features_or_scores_fail_instead_of_zero_fill(monkeypatch, kind):
    class Bad:
        def predict(self, x): return np.full(len(x), np.inf)
    monkeypatch.setattr(ranking, 'load_ranking_model', lambda p=None: (Bad() if kind == 'infinite_score' else Scores(), ['feature'], {}))
    data = panel()
    if kind == 'missing': data = data.drop(columns='feature')
    if kind == 'nan': data.loc[data.trade_date == data.trade_date.max(), 'feature'] = np.nan
    with pytest.raises(ValueError):
        ranking.predict_top_n(data, top_n=2)


def test_bundle_load_uses_saved_ranking_type_without_overwrite(monkeypatch):
    from analysis import model_registry
    def load(path, model_type):
        assert model_type == 'ranking_lgb'
        return dict(model=Scores(), feature_names=['feature']), dict(validation_schema=1, seen_through='2024-01-01')
    monkeypatch.setattr(model_registry, 'load_model', load)
    try:
        model, features, proof = ranking.load_ranking_bundle('explicit-model.pkl')
    except AttributeError:
        pytest.fail('dated ranking bundle loader missing')
    assert features == ['feature']
    assert proof['validation_schema'] == 1
