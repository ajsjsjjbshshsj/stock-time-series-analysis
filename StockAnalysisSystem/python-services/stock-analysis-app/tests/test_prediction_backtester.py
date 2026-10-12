import numpy as np
import pandas as pd
import pytest

from analysis.backtester import Backtester
from analysis.predictor import StockPredictor


class ProbabilityModel:
    def predict_proba(self, values):
        p = np.asarray(values)[:, 0]
        return np.column_stack([1-p, p])


def predictor():
    result = StockPredictor('xgboost')
    result.model, result.feature_names = ProbabilityModel(), ['feature']
    result.train_metadata = dict(validation_schema=1, seen_through='2025-01-02',
                                splits=dict(train=dict(label_end='2025-01-01'), val=dict(label_end='2025-01-02')))
    return result


def frame():
    return pd.DataFrame(dict(ts_code='000001.SZ', trade_date=pd.bdate_range('2025-01-01', periods=7),
                             open=10., close=10., feature=[.9, .9, .6, np.nan, .9, .1, .9]))


def test_probabilities_are_date_aligned_unseen_and_threshold_is_used():
    result = Backtester(1000, 0, 0).run_prediction_backtest(frame(), predictor(), probability_threshold=.8)
    trades = result['trades']
    assert list(trades.date) == [pd.Timestamp('2025-01-08'), pd.Timestamp('2025-01-09')]
    assert list(trades.action) == ['BUY', 'SELL']


def test_no_dated_model_evidence_cannot_claim_sample_outside_training():
    p = predictor()
    p.train_metadata = None
    with pytest.raises(ValueError, match='evidence'):
        Backtester().run_prediction_backtest(frame(), p)


def test_regression_output_is_not_a_probability_strategy():
    p = predictor()
    p.model_type = 'xgboost_regression'
    with pytest.raises(ValueError, match='probability'):
        Backtester().run_prediction_backtest(frame(), p)


def test_inference_preserves_latest_without_future_label():
    p = predictor()
    try:
        samples = p.prepare_inference_frame(frame())
    except AttributeError:
        pytest.fail('date-preserving inference is missing')
    assert samples.trade_date.iloc[-1] == pd.Timestamp('2025-01-09')
    assert pd.Timestamp('2025-01-06') not in set(samples.trade_date)


def test_small_real_dated_training_keeps_sample_outside_seen(monkeypatch):
    from config.settings import MODEL_CONFIG
    monkeypatch.setitem(MODEL_CONFIG, 'xgboost_params', dict(n_estimators=3, max_depth=2, n_jobs=1))
    data = pd.DataFrame(dict(ts_code='000001.SZ', trade_date=pd.bdate_range('2024-01-01', periods=100),
                             close=10+np.arange(100)%3, feature=np.arange(100)%3))
    p = StockPredictor('xgboost')
    try:
        result = p.train_dated(data, feature_names=['feature'])
    except AttributeError:
        pytest.fail('date-bound training missing')
    assert result['test_predictions'].trade_date.min() > pd.Timestamp(p.train_metadata['seen_through'])
    assert p.feature_names == ['feature']
