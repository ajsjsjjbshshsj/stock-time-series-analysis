"""Offline CUDA training and immutable six-model freeze; no future-data claims."""
import argparse
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '8')

import numpy as np
import pandas as pd
import torch

from analysis.training_control import EarlyStopping
from analysis.transformer_experiment import sha256, write_json
from analysis.transformer_features import (feature_columns, load_feature_cache, load_model_preprocessing,
                                          normalize_panel)
from analysis.transformer_forward_freeze import (CUTOFF, VAL_START, SEEDS, MODES, frozen_config,
    frozen_rules, training_boundaries, validate_model_set, validate_panel)
from analysis.transformer_forward_diagnostic import latest_scores, validate_diagnostic
from analysis.transformer_trainer import compute_and_save_features, run_transformer_training
from scripts.run_transformer_fixed_features import read_json
from scripts.run_transformer_portfolio_backtest import load_experiment, digest_json


def validate_paths(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output.parent != APP/'models/transformer' or not output.name.startswith('forward_freeze_')
            or source.is_relative_to(output) or output.is_relative_to(source)):
        raise ValueError('Output must be a separate models/transformer/forward_freeze_* directory')
    return source, output


def source_inputs(source):
    raw, panels, originals, _, upstream = load_experiment(Path(source).resolve(), 1000000.)
    codes = validate_panel(raw)
    configs, config_hashes = {}, {}
    for seed in SEEDS:
        for mode in MODES:
            record = originals[(seed, mode, '2026-08')]
            path = Path(record['model_path']).parent/'config.json'
            saved = read_json(path)
            if saved != record['config']:
                raise ValueError('Actual source saved config differs from audited record')
            frozen_config(saved, Path('unused'), seed, saved['market_preprocessing'])
            configs[(seed, mode)] = saved
            config_hashes[f'{seed}/{mode}'] = sha256(path)
            validate_panel(panels[mode])
    binding = dict(upstream=upstream, saved_config_hashes=config_hashes, cutoff=CUTOFF,
                   validation_start=VAL_START, codes=codes, rules=frozen_rules())
    return panels, configs, binding


def _hash_files(root, excluded=()):
    return {p.relative_to(root).as_posix(): sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and p.name not in excluded}


def artifact_hashes(folder):
    return _hash_files(folder, ('result.json',))


def _old_hashes(output):
    root = APP/'models'
    return {str(p.relative_to(root)): sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.is_relative_to(output)}


def _check_old(output):
    previous = read_json(output/'old_artifact_hashes_before.json')
    for name, digest in previous.items():
        path = APP/'models'/name
        if not path.is_file() or sha256(path) != digest:
            raise ValueError(f'Old artifact changed: {name}')
    return len(previous)


def _bind(output, binding):
    identity = output/'source_identity.json'
    if identity.exists():
        if read_json(identity) != binding:
            raise ValueError('Resume source/config identity mismatch')
        _check_old(output)
    else:
        if output.exists() and any(output.iterdir()):
            raise ValueError('Partial unidentified output preserved; use a fresh directory')
        previous = _old_hashes(output)
        output.mkdir(parents=True, exist_ok=True)
        write_json(output/'old_artifact_hashes_before.json', previous)
        write_json(identity, binding)


def _history(record, folder):
    history = read_json(folder/'model/training_history.json')
    if (history != record['history'] or type(record.get('epochs_completed')) is not int
            or not 1 <= record['epochs_completed'] <= 30 or record['epochs_completed'] != len(history)
            or type(record.get('best_epoch')) is not int):
        raise ValueError('Training history/epoch mismatch')
    stopping = EarlyStopping(5, 1e-6)
    best_epoch, stopped = -1, False
    for i, row in enumerate(history):
        if stopped:
            raise ValueError('History continues beyond early stop')
        value = row['eval_final_score']
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            raise ValueError('Invalid actual validation score')
        improved, stopped = stopping.update(value)
        if improved:
            best_epoch = i+1
    if (record['best_epoch'] != best_epoch or record['best_score'] != stopping.best
            or type(record.get('stopped_early')) is not bool or record['stopped_early'] != stopped):
        raise ValueError('Best checkpoint does not match validation early-stop history')


def validate_record(record, panel, config, folder):
    """Read actual caches/scaler/config and labels, not just report claims."""
    folder = Path(folder)
    model = folder/'model/best_model.pth'
    cache = folder/'model/features_158+39.parquet'
    if (record.get('config') != config or read_json(folder/'model/config.json') != config
            or record.get('input_end') != CUTOFF or record.get('kind') != 'historical_diagnostic'
            or Path(record.get('model_path', '')).resolve() != model.resolve()
            or Path(record.get('scaler_path', '')).resolve() != model.with_name('best_model_scaler.pkl').resolve()
            or record.get('artifact_hashes') != artifact_hashes(folder)):
        raise ValueError('Actual artifact/config/hash/path mismatch')
    _history(record, folder)
    raw, columns, mapping, boundary = load_feature_cache(cache, config)
    if columns != feature_columns('158+39') or record.get('feature_names') != columns or len(columns) != 203:
        raise ValueError('Full feature order mismatch')
    if record.get('stockid2idx') != mapping or set(mapping) != set(panel.ts_code):
        raise ValueError('Stock mapping mismatch')
    boundaries = training_boundaries(raw, columns, boundary)
    if record.get('boundaries') != boundaries:
        raise ValueError('Supervised training boundary mismatch')
    # Check actual raw cache observations and label derivation against the frozen source.
    actual_input = pd.read_parquet(folder/'model/raw_panel.parquet')
    expected_input = normalize_panel(panel)
    try:
        pd.testing.assert_frame_equal(actual_input.reset_index(drop=True), expected_input.reset_index(drop=True),
                                      check_dtype=False)
    except AssertionError as exc:
        raise ValueError('Raw market cache differs from frozen input') from exc
    ordered = raw.sort_values(['股票代码', '日期']).reset_index(drop=True)
    expected = expected_input[['股票代码', '日期', '开盘']].reset_index(drop=True)
    if (len(ordered) != len(expected)
            or not ordered[['股票代码', '日期']].equals(expected[['股票代码', '日期']])
            or not np.array_equal(ordered['开盘'].to_numpy(), expected['开盘'].to_numpy())):
        raise ValueError('Feature cache market identities/prices mismatch')
    grouped = expected.groupby('股票代码')
    target_dates = grouped['日期'].shift(-5)
    labels = (grouped['开盘'].shift(-5)-grouped['开盘'].shift(-1))/(grouped['开盘'].shift(-1)+1e-12)
    if (not ordered.label_target_date.equals(target_dates)
            or not np.allclose(ordered.label.to_numpy(), labels.to_numpy(), equal_nan=True, rtol=1e-9, atol=1e-12)):
        raise ValueError('Actual supervised labels/target dates mismatch')
    scaler, metadata = load_model_preprocessing(model, config)
    if (metadata['selected_features'] != columns or metadata['stockid2idx'] != mapping
            or pd.Timestamp(metadata['feature_history_start']) != raw['日期'].min()
            or {c: pd.Timestamp(d) for c, d in metadata['stock_history_starts'].items()}
                != raw.groupby('股票代码')['日期'].min().to_dict()):
        raise ValueError('Model feature/mapping/history contract mismatch')
    train_mask = (raw.label.notna() & (raw['日期'] < pd.Timestamp(boundary))
                  & (raw.label_target_date < pd.Timestamp(boundary)))
    values = raw.loc[train_mask, columns].to_numpy(dtype=float)
    mean, variance = values.mean(axis=0), values.var(axis=0)
    # StandardScaler treats numerically constant columns as unit-scale, including
    # variance smaller than its floating-point error bound (not only exact zero).
    eps = np.finfo(np.float64).eps
    constant = variance <= len(values)*eps*variance+(len(values)*mean*eps)**2
    scale = np.sqrt(variance)
    scale[constant] = 1.
    if (not np.all(np.asarray(scaler.n_samples_seen_) == len(values))
            or not scaler.with_mean or not scaler.with_std
            or not np.allclose(scaler.mean_, mean, rtol=1e-9, atol=1e-9)
            or not np.allclose(scaler.var_, variance, rtol=1e-9, atol=1e-9)
            or not np.allclose(scaler.scale_, scale, rtol=1e-9, atol=1e-12)):
        raise ValueError('Scaler was not fitted solely on approved training rows')
    rows = read_json(folder/'scores.json')
    diagnostic = validate_diagnostic(rows, sorted(mapping), CUTOFF, sha256(model))
    if record.get('diagnostic') != diagnostic:
        raise ValueError('Latest historical diagnostic mismatch')
    # Weights must actually load with the declared architecture and stock count.
    from analysis.transformer_model import MultiHeadStockTransformer
    checked = MultiHeadStockTransformer(len(columns), config, len(mapping))
    checked.load_state_dict(torch.load(model, map_location='cpu', weights_only=True))


def _folder(output, seed, mode):
    return output/f'seed_{seed}'/mode


def _audit(source, output, require_frozen=True, require_receipt=True):
    panels, configs, binding = source_inputs(source)
    if read_json(output/'source_identity.json') != binding:
        raise ValueError('Frozen source identity mismatch')
    report = read_json(output/'report.json')
    if (report.get('binding') != binding or report.get('rules') != frozen_rules()
            or report.get('status') != 'TRAINED_AWAITING_FUTURE_SIGNALS'
            or report.get('trained') != 6 or report.get('new_blind_observations') != 0):
        raise ValueError('Report identity/rules/state mismatch')
    records = report['models']
    validate_model_set(records)
    for record in records:
        seed, mode = record['seed'], record['mode']
        folder = _folder(output, seed, mode)
        saved = configs[(seed, mode)]
        config = frozen_config(saved, folder, seed, saved['market_preprocessing'])
        if read_json(folder/'result.json') != record:
            raise ValueError('Actual result/report mismatch')
        validate_record(record, panels[mode], config, folder)
    if (output/'SUMMARY.md').read_text(encoding='utf-8') != summary(report):
        raise ValueError('Summary/report mismatch')
    count = _check_old(output)
    if report['old_files_checked'] != count:
        raise ValueError('Old artifact audit count mismatch')
    if require_frozen:
        manifest = read_json(output/'freeze_manifest.json')
        validate_model_set(manifest['models'])
        frozen_at = datetime.fromisoformat(manifest['frozen_at'])
        if (manifest.get('status') != 'FROZEN' or manifest.get('binding') != binding
                or manifest.get('models') != records or manifest.get('rules') != frozen_rules()
                or frozen_at.utcoffset() != timedelta(hours=8)
                or manifest.get('report_sha256') != sha256(output/'report.json')
                or manifest.get('files') != _hash_files(output, ('freeze_manifest.json', 'verification.json'))):
            raise ValueError('Immutable freeze manifest mismatch')
    result = dict(status='PASS', models=6, cutoff=CUTOFF, new_blind_observations=0,
                  old_files_checked=count, changed=[], report_sha256=sha256(output/'report.json'))
    if require_frozen and require_receipt:
        receipt = output/'verification.json'
        expected = dict(result, freeze_manifest_sha256=sha256(output/'freeze_manifest.json'))
        if not receipt.is_file() or read_json(receipt) != expected:
            raise ValueError('Completed freeze receipt/hash mismatch; preserve existing output')
    return result


def verify(source, output):
    result = _audit(source, output)
    print(f"FREEZE_VERIFIED models=6 cutoff={CUTOFF} old_files_unchanged={result['old_files_checked']}", flush=True)
    return result


def summary(report):
    lines = ['# Six-model training and freeze', '',
             'Input cutoff: 2026-09-30; validation boundary: 2026-07-30.',
             'No new blind observations. Last-session scores are historical diagnostics.',
             'All seeds/modes retained; no historical profit-based selection.',
             f"CUDA: {report['gpu']}", '']
    for record in report['models']:
        b = record['boundaries']
        lines.append(f"Seed {record['seed']} / {record['mode']}: epochs={record['epochs_completed']}, "
                     f"best_epoch={record['best_epoch']}, validation final_score={record['best_score']:.8f}; "
                     f"train signal/target end={b['train_signal_end']}/{b['train_target_end']}; "
                     f"validation signal/target end={b['validation_signal_end']}/{b['validation_target_end']}.")
    lines += ['', 'Future signals must be saved before entry; future-data protocol is a separate next stage.',
              'Ideal fractional adjusted units and hypothetical fees, not live execution or profit guarantees.',
              'Fixed20 selection bias; initialization seeds are not independent market observations.', '']
    return '\n'.join(lines)


def run(source, output):
    source, output = validate_paths(source, output)
    if (output/'freeze_manifest.json').exists():
        verify(source, output)
        print('COMPLETE_RESUME no training or artifact writes', flush=True)
        return
    if not torch.cuda.is_available():
        raise RuntimeError('Approved training requires CUDA')
    panels, configs, binding = source_inputs(source)
    _bind(output, binding)
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    records = []
    for seed in SEEDS:
        for mode in MODES:
            folder = _folder(output, seed, mode)
            saved = configs[(seed, mode)]
            config = frozen_config(saved, folder, seed, saved['market_preprocessing'])
            done = folder/'result.json'
            if done.exists():
                record = read_json(done)
                if (record.get('seed'), record.get('mode')) != (seed, mode):
                    raise ValueError('Completed fold identity mismatch')
                validate_record(record, panels[mode], config, folder)
            else:
                if folder.exists() and any(folder.iterdir()):
                    raise ValueError('Partial fold preserved; use a fresh output directory')
                print(f'FREEZE_TRAIN_START seed={seed} mode={mode}', flush=True)
                path, columns, boundary = compute_and_save_features(panels[mode], config=config,
                                                                    use_parallel=True, n_workers=4)
                raw, _, _, _ = load_feature_cache(path, config)
                boundaries = training_boundaries(raw, columns, boundary)
                trained = run_transformer_training(feature_path=path, config=config)
                torch.cuda.synchronize()
                rows = latest_scores(panels[mode], trained, CUTOFF)
                write_json(folder/'scores.json', rows)
                record = dict(trained, seed=seed, mode=mode, input_end=CUTOFF, kind='historical_diagnostic',
                    boundaries=boundaries, diagnostic=validate_diagnostic(rows, binding['codes'], CUTOFF,
                                                                        sha256(trained['model_path'])),
                    artifact_hashes=artifact_hashes(folder))
                validate_record(record, panels[mode], config, folder)
                write_json(done, record)
            records.append(record)
            print(f'FREEZE_TRAIN_COMPLETE seed={seed} mode={mode} epochs={record["epochs_completed"]}', flush=True)
    validate_model_set(records)
    report = dict(binding=binding, rules=frozen_rules(), models=records, trained=6,
                  status='TRAINED_AWAITING_FUTURE_SIGNALS', new_blind_observations=0,
                  gpu=torch.cuda.get_device_name(0), old_files_checked=_check_old(output))
    write_json(output/'report.json', report)
    (output/'SUMMARY.md').write_text(summary(report), encoding='utf-8')
    _audit(source, output, require_frozen=False)
    manifest = dict(status='FROZEN', binding=binding, models=records, rules=frozen_rules(),
                    frozen_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
                    report_sha256=sha256(output/'report.json'),
                    files=_hash_files(output, ('freeze_manifest.json', 'verification.json')))
    write_json(output/'freeze_manifest.json', manifest)
    # First finalization validates the candidate before its receipt exists.
    # Public verify and every completed resume require that saved receipt and
    # never recreate it, so editing even the freeze timestamp is detected.
    result = _audit(source, output, require_receipt=False)
    result['freeze_manifest_sha256'] = sha256(output/'freeze_manifest.json')
    write_json(output/'verification.json', result)
    verify(source, output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('run', 'verify'), required=True)
    parser.add_argument('--source-experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = validate_paths(args.source_experiment, args.output)
    {'run': run, 'verify': verify}[args.stage](source, output)


if __name__ == '__main__':
    main()
