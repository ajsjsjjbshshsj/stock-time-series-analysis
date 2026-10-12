"""Date-preserving labels and purged splits for legacy strategy validation."""
import numpy as np
import pandas as pd

from analysis.pipeline import LABEL_COLUMNS, IDENTITY_COLUMNS, time_split


def dated_samples(frame, feature_names, *, horizon=1, classification=True):
    if (type(horizon) is not int or horizon < 1 or not feature_names
            or len(set(feature_names)) != len(feature_names)
            or set(feature_names) & (LABEL_COLUMNS | IDENTITY_COLUMNS)
            or not {'ts_code', 'trade_date', 'close', *feature_names}.issubset(frame)):
        raise ValueError('Invalid dated strategy features/horizon')
    result = frame.copy()
    result['trade_date'] = pd.to_datetime(result.trade_date, errors='raise')
    if (result[['ts_code', 'trade_date']].isna().any().any()
            or result.duplicated(['ts_code', 'trade_date']).any()
            or result.trade_date.dt.tz is not None
            or not result.trade_date.equals(result.trade_date.dt.normalize())):
        raise ValueError('Invalid sample identities/dates')
    result = result.sort_values(['ts_code', 'trade_date'])
    close = pd.to_numeric(result.close, errors='raise')
    if not np.isfinite(close).all() or (close <= 0).any():
        raise ValueError('Invalid label price history')
    result['close'] = close
    result['label_target_date'] = result.groupby('ts_code').trade_date.shift(-horizon)
    returns = result.groupby('ts_code').close.shift(-horizon)/result.close-1
    result['label'] = (returns > 0).astype(float).where(returns.notna()) if classification else returns
    numeric = result[feature_names].apply(pd.to_numeric, errors='raise')
    valid = np.isfinite(numeric.to_numpy(dtype=float)).all(axis=1) & result.label.notna()
    result.loc[:, feature_names] = numeric
    return result.loc[valid, ['ts_code', 'trade_date', 'label_target_date', 'label', *feature_names]].sort_values(
        ['trade_date', 'ts_code']).reset_index(drop=True)


def purged_splits(samples, *, train_ratio=.6, val_ratio=.2, test_ratio=.2):
    ratios = (train_ratio, val_ratio, test_ratio)
    if any(isinstance(r, bool) or not np.isfinite(r) or not 0 < r < 1 for r in ratios):
        raise ValueError('Invalid split ratios')
    if samples.empty or not {'trade_date', 'label_target_date'}.issubset(samples):
        raise ValueError('Dated labels required')
    frame = samples.copy()
    for col in ('trade_date', 'label_target_date'):
        frame[col] = pd.to_datetime(frame[col], errors='raise')
    if frame[['trade_date', 'label_target_date']].isna().any().any() or (frame.label_target_date <= frame.trade_date).any():
        raise ValueError('Invalid target dates')
    train, val, test = time_split(frame, *ratios)
    if any(f.empty for f in (train, val, test)):
        raise ValueError('Insufficient split history')
    train = train[train.label_target_date < val.trade_date.min()].copy()
    val = val[val.label_target_date < test.trade_date.min()].copy()
    if train.empty or val.empty:
        raise ValueError('Insufficient purged split history')
    groups = dict(train=train, val=val, test=test)
    splits = {key: dict(signal_start=str(f.trade_date.min().date()),
                        signal_end=str(f.trade_date.max().date()),
                        label_end=str(f.label_target_date.max().date()), count=len(f))
              for key, f in groups.items()}
    return dict(groups, splits=splits, seen_through=max(splits[k]['label_end'] for k in ('train', 'val')))


def require_unseen(frame, metadata):
    if not isinstance(metadata, dict) or metadata.get('validation_schema') != 1:
        raise ValueError('Model lacks dated validation evidence; retrain into a new artifact')
    seen = pd.Timestamp(metadata.get('seen_through'))
    if pd.isna(seen) or not isinstance(metadata.get('splits'), dict):
        raise ValueError('Model seen-through evidence missing')
    for key in ('train', 'val'):
        split = metadata['splits'].get(key, {})
        if not split.get('label_end') or pd.Timestamp(split['label_end']) > seen:
            raise ValueError('Model seen-through evidence inconsistent')
    return frame[pd.to_datetime(frame.trade_date) > seen].copy()
