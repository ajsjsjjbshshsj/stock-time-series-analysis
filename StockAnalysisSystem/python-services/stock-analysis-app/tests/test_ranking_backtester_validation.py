import numpy as np
import pandas as pd
import pytest
from analysis import ranking_backtester as backtest
from tests.test_ranking_accounting import inputs


def test_saved_model_without_maturity_evidence_is_rejected(monkeypatch):
    from analysis import ranking_predictor
    class Model:
        def predict(self, x): return np.ones(len(x))
    monkeypatch.setattr(ranking_predictor, 'load_ranking_bundle', lambda p=None: (Model(), ['feature'], {}))
    prices, _, cal = inputs()
    with pytest.raises(ValueError, match='evidence'):
        backtest.run_ranking_backtest(prices.assign(feature=1.), calendar=cal, prices=prices)


def test_walk_forward_never_trains_unmatured_labels(monkeypatch):
    import lightgbm as lgb
    prices, _, cal = inputs(30)
    frame = prices.assign(feature=np.arange(len(prices)) % 30)
    seen = []
    class Model:
        def predict(self, x): return np.ones(len(x))
    def trainer(params, dataset, **kwargs):
        # feature is the stock's day offset, last five days must be absent.
        seen.append(int(np.max(dataset.data)))
        return Model()
    monkeypatch.setattr(lgb, 'train', trainer)
    try:
        result = backtest.run_walk_forward_backtest(frame, ['feature'], train_window=10,
                   top_n=1, rebalance_days=5, calendar=cal, prices=prices, forward_days=5)
    except TypeError as exc: pytest.fail(str(exc))
    signal_days = [pd.Timestamp(row['signal_date']) for row in result['trade_log']]
    offsets = [list(sorted(prices.trade_date.unique())).index(d) for d in signal_days]
    assert len(seen) == len(offsets)
    assert all(last < offset-5 for last, offset in zip(seen, offsets))
    assert result['metrics']['total_return'] < 0  # flat prices plus costs


def test_model_failure_is_not_silently_skipped(monkeypatch):
    import lightgbm as lgb
    prices, _, cal = inputs(30)
    def broken(*args, **kwargs): raise RuntimeError('model failure')
    monkeypatch.setattr(lgb, 'train', broken)
    try:
        with pytest.raises(ValueError, match='signal'):
            backtest.run_walk_forward_backtest(prices.assign(feature=1.), ['feature'], train_window=10,
                     top_n=1, calendar=cal, prices=prices, forward_days=5)
    except TypeError as exc: pytest.fail(str(exc))
