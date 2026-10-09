"""LightGBM ranking with whole-date mature splits and strict inference."""
import os
from datetime import datetime
from uuid import uuid4
import numpy as np
import pandas as pd
import lightgbm as lgb

from config.settings import RANKING_CONFIG
from analysis.pipeline import LABEL_COLUMNS, IDENTITY_COLUMNS
from analysis.strategy_samples import dated_samples, purged_splits
from data_processor.probe_selection import probe_feature_selection

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')


def get_feature_columns(df):
    forbidden = LABEL_COLUMNS | IDENTITY_COLUMNS
    return [c for c in df.select_dtypes(include=[np.number]) if c.lower() not in forbidden]


def train_ranking_model(panel_df, feature_names=None, use_probe=True,
                        probe_config=None, save_name=None, use_gpu=False,
                        forward_days=5, save_model=True):
    names = list(feature_names) if feature_names is not None else get_feature_columns(panel_df)
    samples = dated_samples(panel_df, names, horizon=forward_days, classification=False)
    groups = purged_splits(samples)
    train, val, test = (groups[k] for k in ('train', 'val', 'test'))
    log_records = []
    if use_probe:
        cfg = probe_config or dict(n_iter=RANKING_CONFIG['probe_n_iter'],
                                  n_noise=RANKING_CONFIG['probe_n_noise'], train_ratio=.9, seed=42)
        names, log_records = probe_feature_selection(train, names, output_path=None, **cfg)
        if not names:
            raise ValueError('Probe selected no features')
    params = dict(RANKING_CONFIG['lgb_params'])
    rounds = params.pop('n_estimators', 500)
    params.setdefault('seed', 42)
    if use_gpu: params['device'] = 'gpu'
    model = lgb.train(params, lgb.Dataset(train[names], label=train.label),
                      num_boost_round=rounds,
                      valid_sets=[lgb.Dataset(val[names], label=val.label)],
                      callbacks=[lgb.early_stopping(20, verbose=False), lgb.log_evaluation(0)])
    predicted = np.asarray(model.predict(test[names]), dtype=float)
    if predicted.shape != (len(test),) or not np.isfinite(predicted).all():
        raise ValueError('Invalid model evaluation scores')
    metrics = dict(RMSE=float(np.sqrt(np.mean((test.label.to_numpy()-predicted)**2))),
                   Direction_Accuracy=float((np.sign(test.label.to_numpy()) == np.sign(predicted)).mean()))
    from analysis.model_registry import build_metadata, save_model as reg_save
    name = save_name or ('ranking_model_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex[:8])
    # Never replace the old latest artifact or its evidence.
    if os.path.exists(os.path.join(MODEL_DIR, name+'.pkl')):
        name += '_'+uuid4().hex[:8]
    metadata = build_metadata(name, 'ranking_lgb', names,
                              train_start_date=train.trade_date.min(), train_end_date=train.trade_date.max(),
                              metrics=metrics, params=params, seed=params['seed'],
                              extra=dict(validation_schema=1, horizon=forward_days,
                                         splits=groups['splits'], seen_through=groups['seen_through'],
                                         probe_log=log_records))
    bundle = dict(model=model, feature_names=names, model_type='ranking_lgb',
                  metrics=metrics, validation_metadata=metadata)
    path = reg_save(bundle, metadata, name) if save_model else None
    return dict(model=model, feature_names=names, metrics=metrics, log_records=log_records,
                model_path=path, metadata=metadata)


def load_ranking_bundle(model_path=None):
    from analysis.model_registry import load_model as reg_load
    bundle, metadata = reg_load(model_path, model_type='ranking_lgb')
    if bundle is None: return None, None, None
    return bundle.get('model'), bundle.get('feature_names', []), metadata or {}


def load_ranking_model(model_path=None):
    model, features, metadata = load_ranking_bundle(model_path)
    return model, features, (metadata or {}).get('metrics', {})


def _latest(panel_df, model_path):
    model, names, _ = load_ranking_model(model_path)
    if model is None: raise ValueError('Ranking model unavailable')
    if (panel_df.empty or panel_df.columns.duplicated().any()
            or not names or set(names) & (LABEL_COLUMNS | IDENTITY_COLUMNS)
            or not {'trade_date', 'ts_code', *names}.issubset(panel_df)):
        raise ValueError('Ranking inference feature schema incomplete')
    frame = panel_df.copy()
    frame['trade_date'] = pd.to_datetime(frame.trade_date, errors='raise')
    if frame[['trade_date', 'ts_code']].isna().any().any() or frame.duplicated(['trade_date', 'ts_code']).any():
        raise ValueError('Ranking inference identity invalid')
    latest = frame[frame.trade_date == frame.trade_date.max()].copy()
    if set(latest.ts_code) != set(frame.ts_code):
        raise ValueError('Latest ranking stock pool incomplete')
    values = latest[names].to_numpy(dtype=float)
    if not np.isfinite(values).all(): raise ValueError('Invalid ranking features')
    scores = np.asarray(model.predict(values), dtype=float)
    if scores.shape != (len(latest),) or not np.isfinite(scores).all():
        raise ValueError('Invalid ranking scores')
    latest['预测收益率'] = scores
    latest = latest.sort_values(['预测收益率', 'ts_code'], ascending=[False, True]).reset_index(drop=True)
    latest['排名'] = np.arange(1, len(latest)+1)
    return latest


def predict_top_n(panel_df, model_path=None, top_n=None):
    top_n = RANKING_CONFIG['top_n'] if top_n is None else top_n
    if type(top_n) is not int or top_n < 1: raise ValueError('Invalid top_n')
    latest = _latest(panel_df, model_path)
    columns = ['ts_code', 'trade_date', '预测收益率', '排名']
    if '股票名称' in latest: columns.insert(1, '股票名称')
    return latest[columns].head(top_n)


def predict_full_ranking(panel_df, model_path=None):
    latest = _latest(panel_df, model_path)
    columns = ['ts_code', 'trade_date', '预测收益率', '排名']
    if 'close' in latest: columns.append('close')
    if '股票名称' in latest: columns.insert(1, '股票名称')
    return latest[columns]
