"""Explicit dated model research for the legacy Streamlit backtest button."""
import re
from analysis.strategy_selection import select_strategy_snapshot


def run_dashboard_model_backtest(frame, stock_code, *, calendar, request_time=None,
                                 initial_capital=1000000., commission_rate=.0003):
    """Fit only on purged train/validation history and account unseen dates.

Called only by an explicit UI action. Never saves a model, publishes a result,
fetches data, or replaces old artifacts. Trusted calendar/history are mandatory.
"""
    if not isinstance(stock_code, str):
        raise ValueError('Invalid selected stock identity')
    code = stock_code.strip().upper()
    if re.fullmatch(r'\d{6}', code):
        code += '.SH' if code.startswith('6') else '.SZ'
    if not re.fullmatch(r'\d{6}\.(SH|SZ)', code):
        raise ValueError('Invalid selected stock identity')
    raw, selected = select_strategy_snapshot(frame, calendar, request_time, stock_codes=[code])
    if set(raw.ts_code) != {code}:
        raise ValueError('Model backtest requires the selected single stock')
    from analysis.predictor import StockPredictor
    from analysis.backtester import Backtester
    # Same causal research schema as the existing CLI single-stock backtest.
    names = [column for column in ('ma5', 'ma10', 'rsi', 'macd_dif', 'vol') if column in raw]
    predictor = StockPredictor('xgboost')
    training = predictor.train_dated(raw, feature_names=names)
    account = Backtester(initial_capital=initial_capital, commission_rate=commission_rate)
    result = account.run_prediction_backtest(raw, predictor)
    return dict(result, model_metadata=training['metadata'], data_selection=selected)
