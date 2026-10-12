"""Reject data/identity drift and targets crossing the frozen training boundary."""
import copy
import importlib
import json

import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('analysis.transformer_forward_freeze')


def panel():
    dates = pd.to_datetime(['2023-10-09', '2026-07-29', '2026-07-30', '2026-09-30'])
    return pd.DataFrame([dict(ts_code=f'{i:06}.SZ', trade_date=d,
                             open=10., high=11., low=9., close=10., vol=100., amount=100.)
                         for d in dates for i in range(20)])


def saved_config():
    return dict(feature_num='158+39', use_probe_selection=False, sequence_length=60,
                d_model=128, nhead=4, num_layers=2, dim_feedforward=256, dropout=.1,
                learning_rate=1e-5, max_grad_norm=5., batch_size=8, num_epochs=30,
                early_stopping_patience=5, early_stopping_min_delta=1e-6,
                use_multi_head=True, score_adjustment_policy='nonnegative_variance',
                output_dir='old', seed=42, market_preprocessing={'mode': 'adjusted'})


def raw_features():
    dates = pd.to_datetime(['2026-07-01', '2026-07-20', '2026-07-22', '2026-07-23',
                            '2026-07-27', '2026-07-30', '2026-08-03', '2026-09-24',
                            '2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30'])
    frame = pd.DataFrame([{'日期': d, '股票代码': f'{i:06}.SZ', 'x': float(j), 'label': .01,
                           'label_target_date': dates[j+5] if j+5 < len(dates) else pd.NaT}
                          for i in range(20) for j, d in enumerate(dates)])
    frame.loc[frame.label_target_date.isna(), 'label'] = np.nan
    return frame


def test_complete_panel_codes_are_sorted():
    assert api().validate_panel(panel()) == [f'{i:06}.SZ' for i in range(20)]


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'future', 'zero', 'nan', 'universe', 'start'])
def test_panel_drift_is_rejected(bad):
    frame = panel()
    if bad == 'missing':
        frame = frame.iloc[1:]
    elif bad == 'duplicate':
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif bad == 'future':
        frame.loc[frame.trade_date == frame.trade_date.max(), 'trade_date'] = pd.Timestamp('2026-10-01')
    elif bad in ('zero', 'nan'):
        frame.loc[0, 'open'] = 0. if bad == 'zero' else np.nan
    elif bad == 'universe':
        frame = frame[frame.ts_code != '000000.SZ']
    else:
        frame = frame[frame.trade_date != frame.trade_date.min()]
    with pytest.raises(ValueError):
        api().validate_panel(frame)


def test_config_inherits_saved_values_without_mutating_them(tmp_path):
    saved = saved_config()
    original = copy.deepcopy(saved)
    result = api().frozen_config(saved, tmp_path, 123, {'mode': 'unadjusted_control'})
    assert result['seed'] == 123
    assert result['output_dir'] == str(tmp_path / 'model')
    assert result['market_preprocessing'] == {'mode': 'unadjusted_control'}
    assert {k: v for k, v in result.items() if k not in ('seed', 'output_dir', 'market_preprocessing')} == {
        k: v for k, v in original.items() if k not in ('seed', 'output_dir', 'market_preprocessing')}
    assert saved == original


@pytest.mark.parametrize('key,value', [('batch_size', 2), ('num_epochs', 50), ('use_probe_selection', True),
                                      ('feature_num', '158'), ('learning_rate', np.nan), ('seed', True)])
def test_unapproved_saved_config_is_rejected(tmp_path, key, value):
    config = saved_config()
    config[key] = value
    with pytest.raises(ValueError):
        api().frozen_config(config, tmp_path, 42, {'mode': 'adjusted'})


def test_purge_excludes_target_equal_to_validation_boundary_and_keeps_unlabeled_tail():
    raw = raw_features()
    # July1 targets July30: equality must not enter training; use one earlier target.
    raw.loc[raw['日期'] == pd.Timestamp('2026-07-01'), 'label_target_date'] = pd.Timestamp('2026-07-29')
    result = api().training_boundaries(raw, ['x'], '2026-07-30')
    assert result['train_rows'] == 20
    assert result['validation_rows'] == 40
    assert result['train_signal_end'] == '2026-07-01'
    assert result['train_target_end'] == '2026-07-29'
    assert result['validation_target_end'] == '2026-09-30'
    assert result['unlabeled_dates'] == ['2026-09-24', '2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30']
    assert len(raw) == 240


@pytest.mark.parametrize('bad', ['future_target', 'bad_boundary', 'nonfinite', 'no_train'])
def test_invalid_training_boundaries_fail_closed(bad):
    raw = raw_features()
    boundary = '2026-07-30'
    if bad == 'future_target':
        raw.loc[0, 'label_target_date'] = pd.Timestamp('2026-10-01')
    elif bad == 'bad_boundary':
        boundary = '2026-08-01'
    elif bad == 'nonfinite':
        raw.loc[0, 'x'] = np.inf
    else:
        raw.loc[:, 'label'] = np.nan
    with pytest.raises(ValueError):
        api().training_boundaries(raw, ['x'], boundary)


def test_model_set_requires_exact_six_and_rules_round_trip():
    rows = [dict(seed=seed, mode=mode) for seed in (42, 123, 2026)
            for mode in ('adjusted', 'unadjusted_control')]
    api().validate_model_set(rows)
    for invalid in (rows[:-1], rows + [rows[0]], rows[:-1] + [dict(seed=9, mode='adjusted')]):
        with pytest.raises(ValueError):
            api().validate_model_set(invalid)
    rules = api().frozen_rules()
    assert json.loads(json.dumps(rules, allow_nan=False)) == rules
    assert rules['top_k'] == 5 and rules['exit_offset'] == 5 and rules['signal_step'] == 4
    assert 'start_date' not in rules and 'end_signal_date' not in rules
    assert rules['requires_pre_entry_signal'] is True
