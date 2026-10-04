"""Raw-unit Transformer features; labels never decide inference availability.

Incremental input merges raw stock/date observations, then rebuilds the retained
history. EWM, OBV and EMA cannot be restarted at an arbitrary 80-row boundary.
"""
import json
import hashlib
import multiprocessing as mp
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.preprocessing import StandardScaler
from data_processor.adjusted_market_panel import LEGACY_CONTRACT, validate_market_contract

from analysis.transformer_utils import (
    FEATURE_COLUMNS_MAP, FEATURE_ENGINEER_FUNC_MAP, add_cross_sectional_features,
)

CACHE_VERSION = 2
ALIASES = dict(ts_code='股票代码', trade_date='日期', open='开盘', high='最高',
               low='最低', close='收盘', vol='成交量', amount='成交额')


def normalize_panel(panel):
    """Sort/deduplicate before deriving previous-close fields."""
    frame = panel.copy()
    if frame.columns.duplicated().any():
        raise ValueError('Duplicate market columns')
    for source, target in ALIASES.items():
        if source in frame:
            if target in frame:
                frame = frame.drop(columns=source)
            else:
                frame = frame.rename(columns={source: target})
    required = ['股票代码', '日期', '开盘', '最高', '最低', '收盘', '成交量', '成交额']
    if frame.empty or not set(required).issubset(frame):
        raise ValueError('Empty or incomplete market panel')
    frame['日期'] = pd.to_datetime(frame['日期'], errors='raise')
    if frame[['股票代码', '日期']].isna().any().any():
        raise ValueError('Missing market identity')
    frame['股票代码'] = frame['股票代码'].astype(str)
    frame = frame.drop_duplicates(['股票代码', '日期'], keep='last')
    frame = frame.sort_values(['股票代码', '日期']).reset_index(drop=True)
    for column in required[2:]:
        frame[column] = pd.to_numeric(frame[column], errors='raise').astype(float)
        if not np.isfinite(frame[column]).all():
            raise ValueError('Nonfinite market observations')
    previous = frame.groupby('股票代码')['收盘'].shift(1)
    frame['涨跌额'] = frame['收盘'] - previous
    frame['涨跌幅'] = frame['涨跌额'] / (previous + 1e-12) * 100
    frame['振幅'] = (frame['最高'] - frame['最低']) / (previous + 1e-12) * 100
    return frame


def feature_columns(feature_num):
    if feature_num not in FEATURE_COLUMNS_MAP:
        raise ValueError('Unsupported feature configuration')
    return [name for name in FEATURE_COLUMNS_MAP[feature_num]
            if name not in ('instrument', '股票代码', '日期', 'label')]


def build_feature_panel(panel_df, config, stockid2idx=None, use_parallel=False, n_workers=None, include_labels=True):
    """Compute complete per-stock history, then cross section and optional labels."""
    frame = normalize_panel(panel_df)
    validate_market_contract(frame, config)
    last = frame['日期'].max()
    start = pd.Timestamp(config.get('feature_start_date', last - pd.DateOffset(years=3)))
    frame = frame[frame['日期'] >= start].copy()
    codes = sorted(frame['股票代码'].unique())
    mapping = dict(stockid2idx) if stockid2idx is not None else {code: i for i, code in enumerate(codes)}
    if any(type(i) is not int or i < 0 for i in mapping.values()) or len(set(mapping.values())) != len(mapping):
        raise ValueError('Invalid stock mapping')
    # A supplied model mapping is frozen: unknown stocks are not invented.
    frame = frame[frame['股票代码'].isin(mapping)].copy()
    if 'stock_history_starts' in config:
        starts = frame['股票代码'].map(config['stock_history_starts']).map(pd.Timestamp)
        frame = frame[frame['日期'] >= starts].copy()
    if frame.empty:
        raise ValueError('No stocks known to the supplied mapping')
    columns = feature_columns(config['feature_num'])
    engineer = FEATURE_ENGINEER_FUNC_MAP[config['feature_num']]
    groups = [group.copy() for _, group in frame.groupby('股票代码', sort=False)]
    if use_parallel and len(groups) > 1:
        with mp.Pool(min(n_workers or min(10, mp.cpu_count()), len(groups))) as pool:
            results = pool.map(engineer, groups)
    else:
        results = [engineer(group) for group in groups]
    result = add_cross_sectional_features(pd.concat(results, ignore_index=True))
    result = result.sort_values(['股票代码', '日期']).reset_index(drop=True)
    result['instrument'] = result['股票代码'].map(mapping).astype(np.int64)
    grouped = result.groupby('股票代码', sort=False)
    next_open, fifth_open = grouped['开盘'].shift(-1), grouped['开盘'].shift(-5)
    result['label'] = ((fifth_open - next_open) / (next_open + 1e-12)).where(next_open > 1e-4) if include_labels else np.nan
    result['label_target_date'] = grouped['日期'].shift(-5) if include_labels else pd.NaT
    val_start = (last - pd.DateOffset(months=2)).normalize()
    result['is_val'] = result['日期'] >= val_start
    result.loc[:, columns] = result[columns].replace([np.inf, -np.inf], np.nan)
    if not np.isfinite(result[columns].to_numpy(dtype=float)).all():
        raise ValueError('Invalid raw feature values')
    return result, columns, mapping, val_start.strftime('%Y-%m-%d')


def prepare_inference_data(raw_df, features, scaler):
    """Transform raw units exactly once with a fitted, ordered model scaler."""
    if list(getattr(scaler, 'feature_names_in_', [])) != list(features):
        raise ValueError('Model scaler feature order mismatch; retrain')
    if not features or not np.isfinite(raw_df[features].to_numpy(dtype=float)).all():
        raise ValueError('Invalid inference features')
    result = raw_df.copy()
    result.loc[:, features] = scaler.transform(raw_df[features])
    return result


def prepare_training_data(raw_df, features, val_start):
    """Purge targets touching validation, not their already-known features."""
    boundary = pd.Timestamp(val_start)
    frame = raw_df.copy()
    frame['日期'] = pd.to_datetime(frame['日期'])
    frame['label_target_date'] = pd.to_datetime(frame['label_target_date'])
    mature = np.isfinite(frame['label']) & frame.label_target_date.notna()
    train_mask = mature & (frame['日期'] < boundary) & (frame.label_target_date < boundary)
    val_mask = mature & (frame['日期'] >= boundary)
    if not train_mask.any() or not val_mask.any():
        raise ValueError('Insufficient mature training/validation targets')
    scaler = StandardScaler().fit(frame.loc[train_mask, features])
    context = prepare_inference_data(frame, features, scaler)
    context.loc[~(train_mask | val_mask), 'label'] = np.nan
    return context.loc[train_mask].copy(), context.loc[val_mask].copy(), context, scaler


MODEL_CONFIG_KEYS = ('feature_num', 'sequence_length', 'd_model', 'nhead', 'num_layers',
                     'dim_feedforward', 'dropout', 'use_multi_head')


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def save_model_preprocessing(model_path, scaler, full_features, selected_features, mapping, config, history_start,
                             stock_history_starts):
    """Bind each checkpoint to its own scaler, ordered columns and stock IDs."""
    path = Path(model_path)
    scaler_path = _sidecar(path, '_scaler.pkl')
    joblib.dump(scaler, scaler_path)
    manifest = dict(pipeline_version=CACHE_VERSION, full_features=list(full_features),
                    selected_features=list(selected_features), stockid2idx=mapping,
                    feature_history_start=pd.Timestamp(history_start).isoformat(),
                    stock_history_starts={code: pd.Timestamp(date).isoformat() for code, date in stock_history_starts.items()},
                    config={key: config[key] for key in MODEL_CONFIG_KEYS},
                    market_preprocessing=config.get('market_preprocessing', LEGACY_CONTRACT),
                    model_sha256=_digest(path), scaler_sha256=_digest(scaler_path))
    _sidecar(path, '_preprocessing.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return str(scaler_path)


def load_model_preprocessing(model_path, config, scaler_path=None):
    """No fallback to legacy caches, folder scalers or freshly fitted scalers."""
    path = Path(model_path)
    try:
        manifest = json.loads(_sidecar(path, '_preprocessing.json').read_text(encoding='utf-8'))
        if manifest.get('pipeline_version') != CACHE_VERSION:
            raise ValueError('Unsupported model preprocessing; retrain')
        if manifest['config'] != {key: config[key] for key in MODEL_CONFIG_KEYS}:
            raise ValueError('Model configuration mismatch; use training configuration')
        if manifest.get('market_preprocessing', LEGACY_CONTRACT) != config.get('market_preprocessing', LEGACY_CONTRACT):
            raise ValueError('Model market price contract mismatch')
        if manifest['model_sha256'] != _digest(path):
            raise ValueError('Model checkpoint mismatch; retrain')
        source = Path(scaler_path) if scaler_path else _sidecar(path, '_scaler.pkl')
        if manifest['scaler_sha256'] != _digest(source):
            raise ValueError('Model scaler mismatch; retrain')
        scaler = joblib.load(source)
        full = manifest['full_features']
        selected = manifest['selected_features']
        mapping = manifest['stockid2idx']
        starts = manifest['stock_history_starts']
        if (full != feature_columns(config['feature_num']) or list(scaler.feature_names_in_) != full
                or not selected or len(set(selected)) != len(selected) or not set(selected).issubset(full)
                or not mapping or sorted(mapping.values()) != list(range(len(mapping)))
                or set(starts) != set(mapping)
                or any(pd.isna(pd.Timestamp(date)) for date in starts.values())):
            raise ValueError('Invalid model feature/scaler/stock metadata; retrain')
        pd.Timestamp(manifest['feature_history_start'])
        return scaler, manifest
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError('Model scaler/preprocessing unavailable; retrain') from exc


def _sidecar(path, suffix):
    return Path(path).with_name(Path(path).stem + suffix)


def save_feature_cache(panel_df, save_path, config, use_parallel=True, n_workers=None, incremental=False):
    """Persist raw units and all latest rows; never fit or save a cache scaler."""
    path = Path(save_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = path.parent / 'raw_panel.parquet'
    incoming = normalize_panel(panel_df)
    if incremental and raw_path.exists():
        previous = pd.read_parquet(raw_path, engine='pyarrow')
        incoming = normalize_panel(pd.concat([previous, incoming], ignore_index=True))
    result, columns, mapping, val_start = build_feature_panel(
        incoming, config, use_parallel=use_parallel, n_workers=n_workers)
    # Features are disposable; raw observations are the source for every rebuild.
    incoming.to_parquet(raw_path, engine='pyarrow', index=False)
    result.to_parquet(path, engine='pyarrow', index=False)
    _sidecar(path, '_stockid2idx.json').write_text(json.dumps(mapping, ensure_ascii=False), encoding='utf-8')
    meta = dict(cache_version=CACHE_VERSION, feature_scale='raw', feature_num=config['feature_num'],
                market_preprocessing=config.get('market_preprocessing', LEGACY_CONTRACT),
                feature_cols=columns, val_start_date=val_start, num_stocks=len(mapping))
    _sidecar(path, '_meta.json').write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    return str(path), columns, val_start


def load_feature_cache(save_path, config):
    """Fail closed on legacy/ambiguous units instead of guessing from values."""
    path = Path(save_path)
    try:
        meta = json.loads(_sidecar(path, '_meta.json').read_text(encoding='utf-8'))
        columns = feature_columns(config['feature_num'])
        if meta.get('market_preprocessing', LEGACY_CONTRACT) != config.get('market_preprocessing', LEGACY_CONTRACT):
            raise ValueError('Feature cache market price contract mismatch')
        if (meta.get('cache_version') != CACHE_VERSION or meta.get('feature_scale') != 'raw'
                or meta.get('feature_num') != config['feature_num'] or meta.get('feature_cols') != columns):
            raise ValueError('Unsupported feature cache; rebuild from raw panel')
        mapping = json.loads(_sidecar(path, '_stockid2idx.json').read_text(encoding='utf-8'))
        frame = pd.read_parquet(path, engine='pyarrow')
        if not set(columns + ['日期', '股票代码', 'instrument', 'label', 'label_target_date']).issubset(frame):
            raise ValueError('Incomplete feature cache; rebuild from raw panel')
        frame['日期'] = pd.to_datetime(frame['日期'])
        frame['label_target_date'] = pd.to_datetime(frame['label_target_date'])
        frame['is_val'] = frame['日期'] >= pd.Timestamp(meta['val_start_date'])
        if frame.duplicated(['股票代码', '日期']).any() or not np.isfinite(frame[columns].to_numpy(dtype=float)).all():
            raise ValueError('Invalid feature cache; rebuild from raw panel')
        if not frame['instrument'].equals(frame['股票代码'].map(mapping).astype(np.int64)):
            raise ValueError('Stock mapping mismatch in feature cache; rebuild')
        return frame, columns, mapping, meta['val_start_date']
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Feature cache metadata unavailable; rebuild from raw panel') from exc
