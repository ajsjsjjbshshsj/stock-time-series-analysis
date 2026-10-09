"""Price-driven legacy ranking entrypoints; no prediction-as-return shortcut."""
import numpy as np
import pandas as pd
from config.settings import RANKING_CONFIG
from analysis.strategy_samples import dated_samples, require_unseen
from analysis.ranking_accounting import run_weighted_ranking_account, validate_ranking_history


def _features(frame, names):
    from analysis.pipeline import LABEL_COLUMNS, IDENTITY_COLUMNS
    if not names or set(names) & (LABEL_COLUMNS | IDENTITY_COLUMNS) or not set(names).issubset(frame):
        raise ValueError('Missing/unsafe ranking feature schema')
    values = frame[names].to_numpy(dtype=float)
    if not np.isfinite(values).all(): raise ValueError('Invalid ranking signal features')
    return values


def run_ranking_backtest(panel_df, model_path=None, top_n=None, rebalance_days=None,
                         initial_capital=1000000., *, calendar=None, prices=None):
    from analysis.ranking_predictor import load_ranking_bundle
    model, features, evidence = load_ranking_bundle(model_path)
    if model is None: raise ValueError('Ranking model unavailable')
    unseen = require_unseen(panel_df, evidence)
    if unseen.empty: raise ValueError('No unseen ranking signal history')
    raw = prices if prices is not None else panel_df
    _, dates, _ = validate_ranking_history(raw, calendar)
    unseen['trade_date'] = pd.to_datetime(unseen.trade_date)
    start = str(unseen.trade_date.min().date())
    raw = raw[pd.to_datetime(raw.trade_date) >= pd.Timestamp(start)].copy()
    scores = unseen[['ts_code', 'trade_date']].copy()
    scores['pred_return'] = model.predict(_features(unseen, features))
    return run_weighted_ranking_account(raw, scores, calendar=calendar,
        top_n=RANKING_CONFIG['top_n'] if top_n is None else top_n,
        rebalance_days=RANKING_CONFIG['rebalance_days'] if rebalance_days is None else rebalance_days,
        initial_capital=initial_capital)


def run_walk_forward_backtest(panel_df, feature_names, top_n=10, train_window=365,
                              rebalance_days=5, initial_capital=1000000., *,
                              calendar=None, prices=None, forward_days=5):
    import lightgbm as lgb
    if type(train_window) is not int or train_window < 1 or type(rebalance_days) is not int or rebalance_days < 1:
        raise ValueError('Invalid walk-forward interval')
    raw = prices if prices is not None else panel_df
    _, dates, codes = validate_ranking_history(raw, calendar)
    frame = panel_df.copy()
    frame['trade_date'] = pd.to_datetime(frame.trade_date, errors='raise')
    samples = dated_samples(frame, feature_names, horizon=forward_days, classification=False)
    if len(dates) <= train_window+1: raise ValueError('Insufficient walk-forward history')
    params = dict(RANKING_CONFIG['lgb_params'])
    rounds = params.pop('n_estimators', 500)
    params.setdefault('seed', 42)
    scores = []
    for i in range(train_window, len(dates)-1, rebalance_days):
        day = pd.Timestamp(dates[i])
        recent = pd.to_datetime(dates[i-train_window:i])
        train = samples[samples.trade_date.isin(recent) & (samples.label_target_date < day)]
        signal = frame[frame.trade_date == day]
        if train.empty or set(signal.ts_code) != set(codes) or len(signal) != len(codes):
            raise ValueError('Incomplete mature training/signal history at '+dates[i])
        try:
            # Fixed rounds: no nonexistent validation set for early_stopping.
            model = lgb.train(params, lgb.Dataset(_features(train, feature_names), label=train.label),
                              num_boost_round=rounds, callbacks=[lgb.log_evaluation(0)])
            predicted = np.asarray(model.predict(_features(signal, feature_names)), dtype=float)
            if predicted.shape != (len(signal),) or not np.isfinite(predicted).all():
                raise ValueError('Invalid signal scores')
        except Exception:
            raise ValueError('Ranking model failed at signal '+dates[i]) from None
        scored = signal[['ts_code', 'trade_date']].copy()
        scored['pred_return'] = predicted
        scores.append(scored)
    evaluation = raw[pd.to_datetime(raw.trade_date) >= pd.Timestamp(dates[train_window])].copy()
    return run_weighted_ranking_account(evaluation, pd.concat(scores, ignore_index=True), calendar=calendar,
        top_n=top_n, rebalance_days=rebalance_days, initial_capital=initial_capital)
