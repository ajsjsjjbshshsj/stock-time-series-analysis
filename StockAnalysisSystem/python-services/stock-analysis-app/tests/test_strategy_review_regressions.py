from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
import json
import pickle
import numpy as np
import pandas as pd
import pytest

import main
from analysis import ranking_predictor, model_registry, ranking_backtester
from analysis.strategy_dates import select_data_cutoff
from data_processor import panel_builder
from tests.test_strategy_dates import calendar, daily, TZ
from tests.test_ranking_accounting import inputs


def test_numeric_string_prices_are_checked_numerically():
    frame = daily()
    frame[['open', 'high', 'low', 'close']] = frame[['open', 'high', 'low', 'close']].astype(str)
    result = select_data_cutoff(calendar(), datetime(2026, 10, 9, 17, tzinfo=TZ), frame,
                                ['000001.SZ', '600000.SH'])
    assert result['data_cutoff'] == '2026-10-09'


def test_auxiliary_five_day_labels_keep_five_day_horizon():
    prices, _, _ = inputs(12)
    prices['close'] = np.tile([10, 11, 12, 11, 10, 9, 8, 9, 10, 11, 12, 13], 2)
    result = panel_builder.add_label(prices, forward_days=1)
    first = result[result.ts_code == '000001.SZ'].iloc[0]
    assert first.label == pytest.approx(.1)
    assert first.future_return_5d == pytest.approx(-.1)
    assert first.future_direction_5d == 0
    assert result.groupby('ts_code').tail(5).future_direction_5d.isna().all()


def test_panel_missing_nonsignal_date_cannot_extend_label_horizon():
    prices, _, cal = inputs(30)
    panel = prices.assign(feature=1.)
    panel = panel[panel.trade_date != pd.Timestamp('2025-01-06')]
    with pytest.raises(ValueError, match='coverage'):
        ranking_backtester.run_walk_forward_backtest(panel, ['feature'], train_window=10,
                 top_n=1, calendar=cal, prices=prices, forward_days=1)


class SimpleModel:
    def predict(self, x): return np.ones(len(x))


def test_default_registry_discovers_ranking_model_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(model_registry, 'MODEL_DIR', str(tmp_path))
    model_registry.save_model(dict(model=SimpleModel(), feature_names=['feature']),
          dict(model_type='ranking_lgb', feature_names=['feature'], metrics={}), 'ranking_model_20250101')
    model, names, _ = ranking_predictor.load_ranking_bundle()
    assert model is not None
    assert names == ['feature']


def fake_database(monkeypatch, panel):
    class DB:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        @contextmanager
        def session_scope(self): yield None
    monkeypatch.setattr(panel_builder, 'DatabaseConnector', DB)
    monkeypatch.setattr(panel_builder.repository, 'load_stock_basic_df', lambda s:
                        pd.DataFrame(dict(ts_code=['000001.SZ', '600000.SH'], name=['A', 'B'])))
    monkeypatch.setattr(panel_builder.repository, 'load_daily_panel', lambda *a: panel.copy())


def test_strict_source_loader_does_not_restore_unproven_turnover(monkeypatch):
    prices, _, _ = inputs()
    fake_database(monkeypatch, prices.assign(turnover_rate=np.nan))
    def unsupported(frame, path): return frame.assign(turnover_rate=88.)
    monkeypatch.setattr(panel_builder, 'restore_turnover_rate_from_cache', unsupported)
    try: result = panel_builder.load_all_stock_data_from_db(strict_validation=True)
    except TypeError as exc: pytest.fail(str(exc))
    assert result.turnover_rate.isna().all()
    assert result.attrs['requested_codes'] == ['000001.SZ', '600000.SH']


def test_requested_stock_with_no_history_cannot_disappear(monkeypatch):
    prices, _, cal = inputs()
    fake_database(monkeypatch, prices[prices.ts_code == '000001.SZ'])
    try: loaded = panel_builder.load_all_stock_data_from_db(strict_validation=True)
    except TypeError as exc: pytest.fail(str(exc))
    with pytest.raises(ValueError, match='complete'):
        main.select_strategy_snapshot(loaded.assign(high=11., low=9., vol=100., amount=1000.), cal,
               pd.Timestamp(cal['end']).to_pydatetime().replace(hour=17, tzinfo=TZ))


def test_feature_calculation_cannot_silently_drop_stock(monkeypatch):
    prices, _, cal = inputs(80)
    raw = prices.assign(high=11., low=9., vol=100., amount=1000., turnover_rate=1.)
    raw.attrs['requested_codes'] = ['000001.SZ', '600000.SH']
    monkeypatch.setattr(panel_builder, 'load_all_stock_data_from_db', lambda **kw: raw)
    monkeypatch.setattr(panel_builder, 'prepare_panel_for_training', lambda frame, **kw:
                        frame[frame.ts_code == '000001.SZ'])
    def forbidden(*args, **kwargs): pytest.fail('training shrunk pool must not start')
    monkeypatch.setattr(main, 'train_ranking_model', forbidden)
    with pytest.raises(ValueError, match='coverage'):
        main.run_ranking_pipeline(skip_update=True, calendar=cal,
                  request_time=pd.Timestamp(cal['end']).to_pydatetime().replace(hour=17, tzinfo=TZ))


@pytest.mark.parametrize('model', ['xgboost_regression', 'xgboost_multiclass'])
def test_requested_model_cannot_be_replaced_by_binary_strategy(monkeypatch, model):
    def no_db(): pytest.fail('unsupported model should reject before DB')
    monkeypatch.setattr(main, 'init_database', no_db)
    args = SimpleNamespace(pipeline='traditional', backtest=True, model=model, calendar=calendar(), stock='000001')
    with pytest.raises(ValueError, match='binary'):
        main.cmd_predict(args, None, None)


def test_actual_cli_persists_metrics_and_cutoff(monkeypatch, tmp_path, capsys):
    payload = dict(metrics=dict(total_return=.1), data_selection=dict(target_trade_date='2025-01-17',
        data_cutoff='2025-01-16', fallback_reason='INCOMPLETE_DAILY'),
        execution_assumptions=dict(fill='T+1 open'), model_metadata=dict(model_type='ranking_lgb'))
    monkeypatch.setattr(main, 'run_ranking_backtest_pipeline', lambda **kw: payload)
    monkeypatch.setattr('sys.argv', ['main.py', 'predict', '--pipeline', 'ranking', '--backtest',
                                   '--calendar', 'unused.json', '--report_dir', str(tmp_path)])
    try: main.main()
    except SystemExit as exc: pytest.fail('CLI report export is missing: '+str(exc))
    files = list(tmp_path.rglob('result.json'))
    assert len(files) == 1
    body = json.loads(files[0].read_text(encoding='utf-8'))
    assert body['result']['metrics']['total_return'] == .1
    assert body['result']['data_selection']['data_cutoff'] == '2025-01-16'
    assert 'result.json' in capsys.readouterr().out


def test_ranking_source_fields_used_as_features_participate_in_fallback(monkeypatch):
    prices, _, cal = inputs(80)
    raw = prices.assign(high=11., low=9., vol=100., amount=1000., turnover_rate=1., pe=10.)
    raw.loc[raw.trade_date == raw.trade_date.max(), 'pe'] = np.nan
    raw.attrs['requested_codes'] = ['000001.SZ', '600000.SH']
    monkeypatch.setattr(panel_builder, 'load_all_stock_data_from_db', lambda **kw: raw)
    monkeypatch.setattr(panel_builder, 'prepare_panel_for_training', lambda frame, **kw: frame)
    monkeypatch.setattr(main, 'train_ranking_model', lambda *a, **kw: dict(model_path='mock-model.pkl'))
    monkeypatch.setattr(main, 'predict_top_n', lambda frame, **kw:
                        frame[frame.trade_date == frame.trade_date.max()][['ts_code', 'trade_date']].copy())
    result = main.run_ranking_pipeline(top_n=2, skip_update=True, calendar=cal,
               request_time=pd.Timestamp(cal['end']).to_pydatetime().replace(hour=17, tzinfo=TZ))
    selection = result.attrs['data_selection']
    assert selection['target_trade_date'] == cal['end']
    assert selection['data_cutoff'] == '2025-04-22'
    assert selection['fallback_reason'] == 'INCOMPLETE_DAILY'
