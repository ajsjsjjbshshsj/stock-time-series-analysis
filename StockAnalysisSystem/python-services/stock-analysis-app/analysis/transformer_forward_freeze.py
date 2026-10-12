"""Pure contracts for a fixed six-model training/freeze, not a blind-test result."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pandas as pd

from analysis.portfolio_backtest import POLICIES, SCENARIOS

START, CUTOFF, VAL_START = '2023-10-09', '2026-09-30', '2026-07-30'
SEEDS = (42, 123, 2026)
MODES = ('adjusted', 'unadjusted_control')


def validate_panel(panel):
    """Require a common, unique twenty-stock history with exact approved endpoints."""
    columns = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
    if panel.empty or panel.columns.duplicated().any() or not set(columns).issubset(panel):
        raise ValueError('Incomplete market panel')
    frame = panel[columns].copy()
    dates = pd.to_datetime(frame.trade_date, errors='raise')
    if (frame[['ts_code', 'trade_date']].isna().any().any()
            or frame.duplicated(['ts_code', 'trade_date']).any()
            or dates.min() != pd.Timestamp(START) or dates.max() != pd.Timestamp(CUTOFF)
            or frame.ts_code.nunique() != 20
            or not frame.groupby('trade_date').ts_code.nunique().eq(20).all()):
        raise ValueError('Market cutoff/universe/session identity mismatch')
    values = frame[columns[2:]].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (values[:, :4] <= 0).any()
            or (values[:, 4:] < 0).any()):
        raise ValueError('Invalid market values')
    return sorted(frame.ts_code.unique().tolist())


def frozen_config(saved, folder, seed, contract):
    """Keep actual previously audited parameters, changing only explicit identity fields."""
    expected = dict(feature_num='158+39', use_probe_selection=False, sequence_length=60,
                    d_model=128, nhead=4, num_layers=2, dim_feedforward=256, dropout=.1,
                    learning_rate=1e-5, max_grad_norm=5., batch_size=8, num_epochs=30,
                    early_stopping_patience=5, early_stopping_min_delta=1e-6,
                    use_multi_head=True, score_adjustment_policy='nonnegative_variance')
    if (type(seed) is not int or seed not in SEEDS or type(saved.get('seed')) is not int
            or saved['seed'] not in SEEDS or contract.get('mode') not in MODES
            or any(saved.get(k) != v or (not isinstance(v, bool) and isinstance(saved.get(k), bool))
                   for k, v in expected.items())):
        raise ValueError('Unapproved saved training configuration')
    # Fail rather than silently serializing NaN or drifting optional parameters.
    json.dumps(saved, allow_nan=False)
    result = deepcopy(saved)
    result.update(output_dir=str(Path(folder)/'model'), seed=seed, market_preprocessing=deepcopy(contract))
    return result


def training_boundaries(raw, columns, val_start):
    """Summarize supervised dates without fitting or deleting unlabeled context."""
    frame = raw.copy()
    required = ['日期', '股票代码', 'label', 'label_target_date', *columns]
    if frame.empty or not columns or not set(required).issubset(frame):
        raise ValueError('Missing raw training columns')
    frame['日期'] = pd.to_datetime(frame['日期'], errors='raise')
    frame['label_target_date'] = pd.to_datetime(frame['label_target_date'], errors='raise')
    boundary = pd.Timestamp(val_start)
    dates = sorted(frame['日期'].unique())
    labels = frame.label.to_numpy(dtype=float)
    targets = frame.label_target_date
    if (boundary != pd.Timestamp(VAL_START) or dates[-1] != pd.Timestamp(CUTOFF)
            or frame[['日期', '股票代码']].isna().any().any()
            or frame.duplicated(['股票代码', '日期']).any()
            or not np.isfinite(frame[columns].to_numpy(dtype=float)).all()
            or np.isinf(labels).any() or (targets > pd.Timestamp(CUTOFF)).any()
            or (targets.notna() & (targets <= frame['日期'])).any()
            or not np.array_equal(np.isfinite(labels), targets.notna().to_numpy())):
        raise ValueError('Invalid training date/target/feature boundary')
    codes = frame['股票代码'].nunique()
    if not frame.groupby('日期')['股票代码'].nunique().eq(codes).all():
        raise ValueError('Incomplete feature cross section')
    mature = np.isfinite(labels) & targets.notna()
    train = mature & (frame['日期'] < boundary) & (targets < boundary)
    validation = mature & (frame['日期'] >= boundary)
    tail_dates = sorted(frame.loc[~mature, '日期'].unique())
    if (not train.any() or not validation.any() or len(dates) <= 5
            or tail_dates != dates[-5:]
            or frame.loc[frame['日期'].isin(tail_dates), 'label'].notna().any()):
        raise ValueError('Insufficient mature samples or invalid unlabeled tail')
    def end(mask, name):
        return str(frame.loc[mask, name].max().date())
    return dict(input_end=CUTOFF, validation_start=VAL_START,
                train_rows=int(train.sum()), validation_rows=int(validation.sum()),
                train_signal_end=end(train, '日期'), train_target_end=end(train, 'label_target_date'),
                validation_signal_end=end(validation, '日期'),
                validation_target_end=end(validation, 'label_target_date'),
                unlabeled_dates=[str(pd.Timestamp(d).date()) for d in tail_dates])


def validate_model_set(records):
    identities = [(r.get('seed'), r.get('mode')) for r in records]
    if (len(identities) != 6 or set(identities) != {(s, m) for s in SEEDS for m in MODES}
            or any(type(r.get('seed')) is not int for r in records)):
        raise ValueError('Expected exactly six unique model identities')


def frozen_rules():
    return dict(version='cash_ledger_v1', initial_capital=1000000., top_k=5,
                entry_offset=1, exit_offset=5, signal_step=4, policies=list(POLICIES),
                scenarios=deepcopy(list(SCENARIOS)), tie_break='ts_code_ascending',
                primary_policy='raw', requires_pre_entry_signal=True,
                price_basis='common_adjusted_fractional_units',
                benchmark='same_cost_fixed20_equal_weight',
                status='awaiting_future_protocol_and_prospective_signals')
