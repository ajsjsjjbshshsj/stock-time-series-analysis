from datetime import datetime, timedelta
from types import SimpleNamespace
import pandas as pd
import pytest
import main
from tests.test_ranking_accounting import inputs
from analysis.strategy_dates import SHANGHAI


def data():
    prices, _, cal = inputs(12)
    prices = prices.assign(high=11., low=9., vol=100., amount=1000., feature=1.)
    now = datetime.fromisoformat(cal['end']).replace(hour=17, tzinfo=SHANGHAI)
    return prices, cal, now


def test_ranking_uses_unlabelled_latest_and_explicit_current_model(monkeypatch):
    from data_processor import panel_builder
    frame, cal, now = data()
    monkeypatch.setattr(panel_builder, 'load_all_stock_data_from_db', lambda **kw: frame)
    def prepare(raw, **kwargs):
        assert kwargs['keep_unlabelled'] is True
        return raw.assign(label=float('nan'))
    monkeypatch.setattr(panel_builder, 'prepare_panel_for_training', prepare)
    monkeypatch.setattr(main, 'train_ranking_model', lambda *a, **kw: dict(model_path='current-model.pkl'))
    def predict(raw, *, model_path, top_n):
        assert model_path == 'current-model.pkl'
        return raw[raw.trade_date == raw.trade_date.max()][['ts_code', 'trade_date']]
    monkeypatch.setattr(main, 'predict_top_n', predict)
    try:
        result = main.run_ranking_pipeline(top_n=2, skip_update=True, calendar=cal, request_time=now)
    except TypeError as exc: pytest.fail(str(exc))
    assert result.trade_date.max() == frame.trade_date.max()
    assert result.attrs['data_selection']['data_cutoff'] == cal['end']


def test_walk_forward_does_not_preselect_features_using_full_history(monkeypatch):
    from data_processor import panel_builder
    frame, cal, now = data()
    monkeypatch.setattr(panel_builder, 'load_all_stock_data_from_db', lambda **kw: frame)
    monkeypatch.setattr(panel_builder, 'prepare_panel_for_training', lambda raw, **kw: raw)
    def forbidden(*a, **kw): pytest.fail('full-history training before walk-forward is leakage')
    monkeypatch.setattr(main, 'train_ranking_model', forbidden)
    def account(raw, features, **kwargs):
        assert kwargs['calendar'] == cal
        assert len(kwargs['prices']) == len(frame)
        return dict(metrics=dict(total_return=0.), equity_curve=pd.DataFrame())
    monkeypatch.setattr(main, 'run_walk_forward_backtest', account)
    try:
        result = main.run_ranking_backtest_pipeline(top_n=2, skip_update=True, calendar=cal, request_time=now)
    except TypeError as exc: pytest.fail(str(exc))
    assert result['data_selection']['data_cutoff'] == cal['end']


def test_calendar_missing_is_rejected_before_database_access(monkeypatch):
    from data_processor import panel_builder
    def forbidden(**kwargs): pytest.fail('calendar rejection must precede DB access')
    monkeypatch.setattr(panel_builder, 'load_all_stock_data_from_db', forbidden)
    with pytest.raises(ValueError, match='calendar'):
        main.run_ranking_pipeline(skip_update=True)


def test_cutoff_fallback_preserves_target_and_warning():
    frame, cal, now = data()
    frame = frame.drop(frame[(frame.ts_code == '000001.SZ') & (frame.trade_date == frame.trade_date.max())].index)
    try:
        selected, proof = main.select_strategy_snapshot(frame, cal, now)
    except AttributeError: pytest.fail('local cutoff selection missing')
    assert proof['target_trade_date'] == cal['end']
    assert proof['fallback_reason'] == 'INCOMPLETE_DAILY'
    assert selected.trade_date.max().strftime('%Y-%m-%d') == proof['data_cutoff']


def test_absent_lstm_trainer_is_explicit_not_attribute_error():
    with pytest.raises(ValueError, match='LSTM'):
        main.run_prediction(pd.DataFrame(), model_type='lstm')


@pytest.mark.parametrize('command', ['cmd_train', 'cmd_predict'])
@pytest.mark.parametrize('backtest', [False, True])
def test_cli_dispatch_forwards_trusted_calendar(monkeypatch, command, backtest):
    _, cal, _ = data()
    args = SimpleNamespace(pipeline='ranking', backtest=backtest, top_n=2, no_probe=True,
        forward_days=5, train_window=10, rebalance_days=5, use_tushare=False, delay=.5,
        skip_update=True, gpu=False, n_workers=1, calendar=cal)
    received = []
    def pipeline(**kwargs): received.append(kwargs)
    monkeypatch.setattr(main, 'run_ranking_pipeline', pipeline)
    monkeypatch.setattr(main, 'run_ranking_backtest_pipeline', pipeline)
    getattr(main, command)(args, None, None)
    assert received[0].get('calendar') == cal


def test_traditional_validated_backtest_rejects_calendar_before_db(monkeypatch):
    def no_db(): pytest.fail('DB initialization before validation rejection')
    monkeypatch.setattr(main, 'init_database', no_db)
    args = SimpleNamespace(pipeline='traditional', backtest=True, calendar=None, model='xgboost', stock='000001')
    with pytest.raises(ValueError, match='calendar'):
        main.cmd_predict(args, None, None)
