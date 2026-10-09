"""Offline score ablation and fixed-feature CUDA comparison; no provider imports."""
import argparse
import json
import os
from pathlib import Path
import sys
from numbers import Real
import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '8')

import pandas as pd
from analysis.transformer_experiment import load_source, sha256, write_json, evaluate_month, summarize, split_month
from analysis.transformer_features import feature_columns, load_model_preprocessing
from analysis.transformer_scoring import POLICIES, score_policy
from data_processor.adjusted_market_panel import build_model_panel
from scripts.run_transformer_adjusted_experiment import (
    experiment_config, validate_fold_identity, validate_fold_metrics,
)

MODES = ('adjusted', 'unadjusted_control')
MONTHS = ('2026-06', '2026-07', '2026-08')
OLD_MONTHS = (*MONTHS, '2026-09')


def fixed_config(folder, contract):
    return dict(experiment_config(folder, contract), use_probe_selection=False,
                score_adjustment_policy='nonnegative_variance')


def validate_fixed_features(record, metadata=None):
    if (record['config'].get('use_probe_selection', True)
            or record['feature_names'] != feature_columns('158+39')):
        raise ValueError('Fixed feature order/probe contract mismatch')
    if metadata is not None and metadata['selected_features'] != record['feature_names']:
        raise ValueError('Actual model selected feature order mismatch')


def validate_identities(records, months):
    identities = [(r['mode'], r['month']) for r in records]
    expected = {(mode, month) for mode in MODES for month in months}
    if len(identities) != len(expected) or set(identities) != expected:
        raise ValueError('Duplicate or incomplete fold identity set')


def validate_paths(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output.parent != APP/'models/transformer' or not output.name.startswith('fixed_features_')
            or source.is_relative_to(output) or output.is_relative_to(source)):
        raise ValueError('Output must be a separate models/transformer/fixed_features_* directory')
    return source, output


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_data(source):
    """Validate original report and frozen checkpoints without rewriting verification."""
    report = read_json(source/'report.json')
    identity = read_json(source/'source_identity.json')
    panel, manifest = load_source(identity['source'])
    factor_hash = sha256(source/'factors.parquet')
    if (report['source_sha256'] != manifest['snapshot_sha256']
            or identity['snapshot_sha256'] != manifest['snapshot_sha256']
            or report['factor_sha256'] != factor_hash):
        raise ValueError('Original source/factor hashes mismatch')
    factors = pd.read_parquet(source/'factors.parquet')
    panels, contracts = {}, {}
    for mode in MODES:
        panels[mode], contracts[mode] = build_model_panel(panel, factors, mode=mode,
                                                         factor_snapshot_sha256=factor_hash)
    validate_identities(report['folds'], OLD_MONTHS)
    for record in report['folds']:
        folder = source/record['mode']/record['month']
        training, _ = split_month(panel, record['month'])
        validate_fold_identity(record, record['mode'], record['month'], folder,
                               experiment_config(folder, contracts[record['mode']]), training.trade_date.max())
        _, metadata = load_model_preprocessing(record['model_path'], record['config'])
        if metadata['selected_features'] != record['feature_names']:
            raise ValueError('Original actual feature order mismatch')
        validate_fold_metrics(record, read_json(folder/'daily_metrics.json'), panel)
    binding = dict(source=str(source), report_sha256=sha256(source/'report.json'),
                   source_sha256=manifest['snapshot_sha256'], factor_sha256=factor_hash)
    return panel, panels, contracts, report, binding


def bind_output(output, binding, initialize=False):
    identity = output/'source_identity.json'
    if identity.exists():
        if read_json(identity) != binding:
            raise ValueError('Resume source identity mismatch')
    elif initialize:
        if output.exists() and any(output.iterdir()):
            raise ValueError('Preserve unidentified partial output; use a fresh directory')
        output.mkdir(parents=True, exist_ok=True)
        hashes = {str(path.relative_to(APP/'models')): sha256(path)
                  for path in (APP/'models').rglob('*')
                  if path.is_file() and not path.is_relative_to(output)}
        write_json(output/'old_artifact_hashes_before.json', hashes)
        write_json(identity, binding)
    else:
        raise ValueError('Run ablate first to initialize audited output')


def check_old_artifacts(output):
    hashes = read_json(output/'old_artifact_hashes_before.json')
    changed = [name for name, digest in hashes.items()
               if not (APP/'models'/name).is_file() or sha256(APP/'models'/name) != digest]
    if changed:
        raise ValueError(f'Original artifacts changed: {changed}')
    return len(hashes)


def validate_scored_record(record, rows, panel):
    validate_fold_metrics(record, rows, panel)
    for row in rows:
        for policy in POLICIES:
            prefix = f'policy_{policy}_'
            if any(key not in row for key in (prefix+'top5_return', prefix+'excess', prefix+'top5_changed')):
                raise ValueError('Incomplete score policy metrics')
            if row[prefix+'top5_changed'] not in (0, 1):
                raise ValueError('Invalid Top5 change indicator')
            keys = [prefix+'top5_return', prefix+'excess', 'common_adjusted_label_equal_weight_return']
            if any(not isinstance(row.get(key), Real) or isinstance(row[key], bool)
                   or not np.isfinite(row[key]) for key in keys):
                raise ValueError('Score policy coverage requires finite numeric values')
            if not np.isclose(row[prefix+'excess'], row[prefix+'top5_return']
                              - row['common_adjusted_label_equal_weight_return'], rtol=1e-7, atol=1e-9):
                raise ValueError('Score policy excess inconsistent with common labels')
        bound_prefix = f'policy_{score_policy(record["config"])}_'
        for policy_key, common_key in [('top5_return', 'adjusted_top5_return'), ('excess', 'adjusted_excess')]:
            value = row.get('common_adjusted_label_'+common_key)
            if not isinstance(value, Real) or not np.isfinite(value) or not np.isclose(
                    row[bound_prefix+policy_key], value, rtol=1e-7, atol=1e-9):
                raise ValueError('Checkpoint-bound policy metric mismatch')


def validate_forecast(path, metadata, *, universe, inference_date, source_sha256, model_sha256, policy):
    expected = dict(inference_date=inference_date, source_sha256=source_sha256,
                    model_sha256=model_sha256, score_policy=policy, csv_sha256=sha256(path))
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError('Forecast provenance/hash mismatch')
    forecast = pd.read_csv(path)
    if (len(forecast) != 5 or forecast['股票代码'].nunique() != 5
            or not set(forecast['股票代码']).issubset(universe)
            or list(forecast['排名']) != list(range(1, 6))):
        raise ValueError('Forecast universe/rank mismatch')
    values = forecast[['预测分数', '调整后分数']].apply(pd.to_numeric, errors='coerce')
    column = '预测分数' if policy == 'raw' else '调整后分数'
    if not np.isfinite(values).all().all() or not values[column].is_monotonic_decreasing:
        raise ValueError('Forecast score/order invalid')


def write_latest_forecasts(folder, record, panel, binding):
    """Recompute diagnostic artifacts and bind their exact bytes; no old writes."""
    from analysis.transformer_trainer import predict_top_stocks_transformer
    for policy in POLICIES:
        forecast = predict_top_stocks_transformer(panel_df=panel, model_path=record['model_path'],
            config=record['config'], score_policy_override=policy, create_plots=False)
        if forecast is None or len(forecast) != 5:
            raise ValueError('Latest diagnostic forecast unavailable')
        forecast.to_csv(folder/f'latest_{policy}.csv', index=False, encoding='utf-8-sig')
    raw = predict_top_stocks_transformer(panel_df=panel, model_path=record['model_path'],
        config=record['config'], top_k=20, create_plots=False)
    raw = raw.sort_values('预测分数', ascending=False, kind='stable').head(5).copy()
    raw['排名'] = range(1, 6)
    raw['调整后分数'] = raw['预测分数']
    raw.drop(columns=['权重'], errors='ignore').to_csv(folder/'latest_raw.csv', index=False, encoding='utf-8-sig')
    date = str(pd.to_datetime(panel.trade_date).max().date())
    record['latest_inference_date'] = date
    record['forecasts'] = {}
    for policy in ('raw', *POLICIES):
        path = folder/f'latest_{policy}.csv'
        provenance = dict(csv_sha256=sha256(path), inference_date=date, source_sha256=binding['source_sha256'],
                          model_sha256=sha256(record['model_path']), score_policy=policy)
        validate_forecast(path, provenance, universe=set(panel.ts_code), inference_date=date,
                          source_sha256=binding['source_sha256'], model_sha256=provenance['model_sha256'], policy=policy)
        record['forecasts'][policy] = provenance


def aggregate(records, output, section):
    result = {}
    for mode in MODES:
        rows = []
        for month in MONTHS:
            rows.extend(read_json(output/section/mode/month/'daily_metrics.json'))
        result[mode] = summarize(rows)
    return result


def ablate(source, output):
    panel, panels, _, report, binding = source_data(source)
    bind_output(output, binding, initialize=True)
    records = []
    for original in report['folds']:
        mode, month = original['mode'], original['month']
        folder = output/'ablation'/mode/month
        if (folder/'result.json').exists():
            record = read_json(folder/'result.json')
            if record['model_path'] != original['model_path'] or record['config'] != original['config']:
                raise ValueError('Ablation resume checkpoint/config mismatch')
            validate_scored_record(record, read_json(folder/'daily_metrics.json'), panel)
        else:
            if folder.exists():
                raise ValueError('Partial ablation preserved; use a fresh output directory')
            folder.mkdir(parents=True)
            rows = evaluate_month(panels[mode], original, month, panels['adjusted'], POLICIES)
            record = dict(original, summary=summarize(rows), scoring_policies=list(POLICIES))
            write_json(folder/'daily_metrics.json', rows)
            if month == '2026-09':
                write_latest_forecasts(folder, record, panels[mode], binding)
            write_json(folder/'result.json', record)
        records.append(record)
        print(f'ABLATION_COMPLETE mode={mode} month={month}', flush=True)
    validate_identities(records, OLD_MONTHS)
    write_json(output/'ablation_report.json', dict(binding=binding, folds=records,
               rolling_aggregate=aggregate(records, output, 'ablation'), old_files_checked=check_old_artifacts(output)))


def train(source, output):
    import torch
    from analysis.transformer_trainer import compute_and_save_features, run_transformer_training
    if not torch.cuda.is_available():
        raise RuntimeError('Approved experiment requires CUDA')
    panel, panels, contracts, _, binding = source_data(source)
    bind_output(output, binding)
    if not (output/'ablation_report.json').exists():
        raise ValueError('Complete score ablation before training')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    records = []
    for mode in MODES:
        for month in MONTHS:
            folder = output/'fixed'/mode/month
            training, _ = split_month(panels[mode], month)
            config = fixed_config(folder, contracts[mode])
            if (folder/'result.json').exists():
                record = read_json(folder/'result.json')
                validate_fold_identity(record, mode, month, folder, config, training.trade_date.max())
                _, metadata = load_model_preprocessing(record['model_path'], config)
                validate_fixed_features(record, metadata)
                validate_scored_record(record, read_json(folder/'daily_metrics.json'), panel)
            else:
                if folder.exists():
                    raise ValueError('Partial training preserved; use a fresh output directory')
                folder.mkdir(parents=True)
                print(f'FIXED_START mode={mode} month={month}', flush=True)
                feature_path, _, _ = compute_and_save_features(training, config=config, use_parallel=True, n_workers=4)
                trained = run_transformer_training(feature_path=feature_path, config=config)
                _, metadata = load_model_preprocessing(trained['model_path'], config)
                validate_fixed_features(trained, metadata)
                rows = evaluate_month(panels[mode], trained, month, panels['adjusted'], POLICIES)
                record = dict(trained, mode=mode, month=month, train_end=str(training.trade_date.max().date()),
                              summary=summarize(rows), scoring_policies=list(POLICIES))
                write_json(folder/'daily_metrics.json', rows)
                write_json(folder/'result.json', record)
            validate_fixed_features(record)
            records.append(record)
            print(f'FIXED_COMPLETE mode={mode} month={month} features={len(record["feature_names"])}', flush=True)
    write_json(output/'fixed_report.json', dict(binding=binding, gpu=torch.cuda.get_device_name(0),
               folds=records, rolling_aggregate=aggregate(records, output, 'fixed'),
               limitations=['known diagnostic months, not blind testing', 'single seed42; no robustness claim',
               'joint price and label changes, not isolated causal effects', 'no fees/execution/capital simulation',
               'overlapping label mean is not cumulative return', 'fixed20-stock pool has selection bias']))
    verify(source, output)


def verify(source, output):
    import numpy as np
    panel, _, contracts, original, binding = source_data(source)
    bind_output(output, binding)
    originals = {(r['mode'], r['month']): r for r in original['folds']}
    for section, filename, months in [('ablation', 'ablation_report.json', OLD_MONTHS),
                                      ('fixed', 'fixed_report.json', MONTHS)]:
        report = read_json(output/filename)
        if report['binding'] != binding:
            raise ValueError('Report source identity mismatch')
        validate_identities(report['folds'], months)
        for record in report['folds']:
            mode, month = record['mode'], record['month']
            if section == 'fixed':
                folder = output/section/mode/month
                config = fixed_config(folder, contracts[mode])
                training, _ = split_month(panel, month)
                validate_fold_identity(record, mode, month, folder, config, training.trade_date.max())
                validate_fixed_features(record)
            else:
                prior = originals[(mode, month)]
                if any(record[key] != prior[key] for key in ('model_path', 'scaler_path', 'config', 'feature_names')):
                    raise ValueError('Ablation original model identity mismatch')
            if record.get('scoring_policies') != list(POLICIES):
                raise ValueError('Score policy report mismatch')
            _, metadata = load_model_preprocessing(record['model_path'], record['config'])
            if metadata['selected_features'] != record['feature_names']:
                raise ValueError('Actual checkpoint feature order mismatch')
            if section == 'fixed':
                validate_fixed_features(record, metadata)
            validate_scored_record(record, read_json(output/section/mode/month/'daily_metrics.json'), panel)
        if report['rolling_aggregate'] != aggregate(report['folds'], output, section):
            raise ValueError('Rolling aggregate mismatch')
    for mode in MODES:
        record = next(r for r in read_json(output/'ablation_report.json')['folds']
                      if r['mode'] == mode and r['month'] == '2026-09')
        date = str(panel.trade_date.max().date())
        if record.get('latest_inference_date') != date or set(record.get('forecasts', {})) != {'raw', *POLICIES}:
            raise ValueError('Forecast date/policy provenance incomplete')
        _, metadata = load_model_preprocessing(record['model_path'], record['config'])
        universe = set(metadata['stockid2idx']) & set(panel.loc[panel.trade_date == panel.trade_date.max(), 'ts_code'])
        for policy in ('raw', *POLICIES):
            validate_forecast(output/'ablation'/mode/'2026-09'/f'latest_{policy}.csv', record['forecasts'][policy],
                universe=universe, inference_date=date, source_sha256=binding['source_sha256'],
                model_sha256=sha256(record['model_path']), policy=policy)
    count = check_old_artifacts(output)
    write_json(output/'verification.json', dict(status='PASS', old_files_checked=count,
               changed=[], ablation_folds=8, fixed_folds=6, features=203, binding=binding))
    print(f'VERIFIED ablation=8 fixed=6 features=203 old_files_unchanged={count}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('ablate', 'train', 'verify'), required=True)
    parser.add_argument('--source-experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = validate_paths(args.source_experiment, args.output)
    {'ablate': ablate, 'train': train, 'verify': verify}[args.stage](source, output)


if __name__ == '__main__':
    main()
