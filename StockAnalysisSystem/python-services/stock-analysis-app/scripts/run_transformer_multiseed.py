"""Audited offline three-seed sensitivity: six read-only reused + twelve fresh CUDA fits."""
import argparse
import os
from pathlib import Path
import sys
import time

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '8')

from analysis.transformer_multiseed import (
    SEEDS, MODES, MONTHS, METRICS, aggregate_seeds, validate_identities, validate_rows,
)
from analysis.transformer_experiment import sha256, write_json, evaluate_month, summarize, split_month
from analysis.transformer_features import load_model_preprocessing
from analysis.transformer_scoring import POLICIES
from scripts.run_transformer_fixed_features import (
    fixed_config, validate_fixed_features, validate_scored_record, source_data,
    bind_output, check_old_artifacts, read_json,
    validate_identities as validate_source_identities,
)
from scripts.run_transformer_adjusted_experiment import validate_fold_identity

LIMITATIONS = [
    'Three initialization seeds are sensitivity checks, not independent market samples or reliable confidence intervals.',
    'June-August are known diagnostic months, not a new blind holdout; never select seed by these results.',
    '65 shared dates per seed; overlapping T+1-open to T+5-open label means are not cumulative portfolio returns.',
    'Fixed20 pool selection bias, historical data revisions, single market interval; no fees/execution/capital constraints.',
    'Raw ranking is predeclared primary; nonnegative_variance secondary; legacy_variance diagnostic.',
]


def seed_config(folder, contract, seed):
    if seed not in SEEDS:
        raise ValueError('Unapproved seed')
    return dict(fixed_config(folder, contract), seed=seed)


def artifact_hashes(record):
    model = Path(record['model_path'])
    paths = dict(model=model, scaler=Path(record['scaler_path']),
                 preprocessing=model.with_name(model.stem+'_preprocessing.json'))
    return {key: sha256(path) for key, path in paths.items()}


def validate_provenance(record, seed, original=None):
    if record.get('seed') != seed or record['config'].get('seed') != seed:
        raise ValueError('Record/config seed mismatch')
    if record.get('origin') != ('reused' if seed == 42 else 'trained'):
        raise ValueError('Seed origin provenance mismatch')
    if record.get('artifact_hashes') != artifact_hashes(record):
        raise ValueError('Actual artifact hash mismatch')
    if seed == 42:
        if original is None or {k: v for k, v in record.items()
                                if k not in ('seed', 'origin', 'artifact_hashes')} != original:
            raise ValueError('Read-only reuse source record mismatch')


def prepare_fold(folder):
    """Only a complete record permits resume; never overwrite partial evidence."""
    done = folder/'result.json'
    if done.is_file():
        return read_json(done)
    if folder.exists():
        raise ValueError(f'Partial fold preserved at {folder}; use a fresh output directory')
    folder.mkdir(parents=True)
    return None


def validate_paths(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output.parent != APP/'models/transformer' or not output.name.startswith('multiseed_')
            or source.is_relative_to(output) or output.is_relative_to(source)):
        raise ValueError('Output must be a separate models/transformer/multiseed_* directory')
    return source, output


def source_inputs(source):
    """All old artifacts are read-only; do not call source runners' writing verify()."""
    fixed_report = read_json(source/'fixed_report.json')
    prior_binding = read_json(source/'source_identity.json')
    panel, panels, contracts, _, upstream = source_data(Path(prior_binding['source']))
    if fixed_report['binding'] != upstream or prior_binding != upstream:
        raise ValueError('Fixed experiment source binding mismatch')
    validate_source_identities(fixed_report['folds'], MONTHS)
    originals, source_daily = {}, {}
    for record in fixed_report['folds']:
        mode, month = record['mode'], record['month']
        folder = source/'fixed'/mode/month
        if read_json(folder/'result.json') != record:
            raise ValueError('Fixed source record/report mismatch')
        training, _ = split_month(panel, month)
        validate_fold_identity(record, mode, month, folder, seed_config(folder, contracts[mode], 42),
                               training.trade_date.max())
        _, metadata = load_model_preprocessing(record['model_path'], record['config'])
        validate_fixed_features(record, metadata)
        if record.get('scoring_policies') != list(POLICIES):
            raise ValueError('Fixed source policy coverage mismatch')
        rows = read_json(folder/'daily_metrics.json')
        validate_scored_record(record, rows, panel)
        validate_rows(rows, month)
        originals[(mode, month)], source_daily[(mode, month)] = record, rows
    binding = dict(source=str(source), fixed_report_sha256=sha256(source/'fixed_report.json'),
                   upstream=upstream, source_sha256=upstream['source_sha256'],
                   factor_sha256=upstream['factor_sha256'], seeds=list(SEEDS), months=list(MONTHS))
    return panel, panels, contracts, originals, source_daily, binding


def fold_folder(output, seed, mode, month):
    return output/f'seed_{seed}'/mode/month


def validate_record(record, rows, *, seed, mode, month, source, output, panel, contracts,
                    originals, source_daily):
    original = originals[(mode, month)] if seed == 42 else None
    validate_provenance(record, seed, original)
    folder = source/'fixed'/mode/month if seed == 42 else fold_folder(output, seed, mode, month)
    training, _ = split_month(panel, month)
    validate_fold_identity(record, mode, month, folder, seed_config(folder, contracts[mode], seed),
                           training.trade_date.max())
    _, metadata = load_model_preprocessing(record['model_path'], record['config'])
    validate_fixed_features(record, metadata)
    if record.get('scoring_policies') != list(POLICIES):
        raise ValueError('Scoring policy set mismatch')
    validate_scored_record(record, rows, panel)
    validate_rows(rows, month)
    if seed == 42 and rows != source_daily[(mode, month)]:
        raise ValueError('Read-only reuse daily metric mismatch')


def _summary_text(report):
    aggregate = report['aggregate']
    lines = ['# Three-seed sensitivity (2026-10-05)', '',
             '18 folds: 6 reused seed42, 12 newly trained; 203 ordered features.',
             '65 common mature dates within each seed; statistics across 3 seed means (sample SD).',
             'All returns below are overlapping horizon means, NOT cumulative portfolio returns.', '']
    for mode in MODES:
        lines.append(f'## {mode}')
        lines.append('')
        for seed in SEEDS:
            row = aggregate['per_seed'][str(seed)][mode]
            lines.append(f'Seed {seed}: raw Top5={row[METRICS[0]]:.6%}; raw excess={row[METRICS[1]]:.6%}; '
                         f'RankIC={row[METRICS[2]]:.6f}; nonnegative Top5={row[METRICS[5]]:.6%}.')
        for key in (METRICS[0], METRICS[1], METRICS[2], METRICS[5], METRICS[6]):
            stats = aggregate['across_seeds'][mode][key]
            lines.append(f'{key}: mean={stats["mean"]:.8f}, sample SD={stats["sample_std"]:.8f}, '
                         f'min={stats["min"]:.8f}, max={stats["max"]:.8f}.')
        lines.append('Positive-excess seeds: '+str(aggregate['positive_excess_seeds'][mode]))
        lines.append('')
    lines += ['## Limitations', '', *['- '+item for item in LIMITATIONS]]
    return '\n'.join(lines)+'\n'


def run(source, output):
    import torch
    from analysis.transformer_trainer import compute_and_save_features, run_transformer_training
    if not torch.cuda.is_available():
        raise RuntimeError('Approved experiment requires CUDA')
    panel, panels, contracts, originals, source_daily, binding = source_inputs(source)
    bind_output(output, binding, initialize=True)
    check_old_artifacts(output)
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    records, daily = [], {}
    for seed in SEEDS:
        for mode in MODES:
            for month in MONTHS:
                folder = fold_folder(output, seed, mode, month)
                record = prepare_fold(folder)
                if record is not None:
                    rows = read_json(folder/'daily_metrics.json')
                elif seed == 42:
                    original = originals[(mode, month)]
                    record = dict(original, seed=42, origin='reused', artifact_hashes=artifact_hashes(original))
                    rows = source_daily[(mode, month)]
                else:
                    training, _ = split_month(panels[mode], month)
                    config = seed_config(folder, contracts[mode], seed)
                    print(f'MULTISEED_START seed={seed} mode={mode} month={month}', flush=True)
                    started = time.perf_counter()
                    feature_path, _, _ = compute_and_save_features(training, config=config,
                                                                   use_parallel=True, n_workers=4)
                    trained = run_transformer_training(feature_path=feature_path, config=config)
                    torch.cuda.synchronize()
                    _, metadata = load_model_preprocessing(trained['model_path'], config)
                    validate_fixed_features(trained, metadata)
                    rows = evaluate_month(panels[mode], trained, month, panels['adjusted'], POLICIES)
                    record = dict(trained, seed=seed, origin='trained', mode=mode, month=month,
                                  train_end=str(training.trade_date.max().date()), summary=summarize(rows),
                                  scoring_policies=list(POLICIES), training_seconds=time.perf_counter()-started,
                                  artifact_hashes=artifact_hashes(trained))
                validate_record(record, rows, seed=seed, mode=mode, month=month, source=source,
                                output=output, panel=panel, contracts=contracts, originals=originals,
                                source_daily=source_daily)
                # Write only to the new output. Existing completed folds stay read-only.
                if not (folder/'result.json').exists():
                    write_json(folder/'daily_metrics.json', rows)
                    write_json(folder/'result.json', record)
                records.append(record)
                daily[(seed, mode, month)] = rows
                print(f'MULTISEED_COMPLETE seed={seed} mode={mode} month={month} origin={record["origin"]}', flush=True)
    report = dict(binding=binding, gpu=torch.cuda.get_device_name(0), folds=records,
                  reused=6, trained=12, features=203, aggregate=aggregate_seeds(records, daily),
                  limitations=LIMITATIONS, old_files_checked=check_old_artifacts(output))
    write_json(output/'report.json', report)
    (output/'SUMMARY.md').write_text(_summary_text(report), encoding='utf-8')
    verify(source, output)


def verify(source, output):
    panel, _, contracts, originals, source_daily, binding = source_inputs(source)
    bind_output(output, binding)
    report = read_json(output/'report.json')
    if (report['binding'] != binding or report.get('reused') != 6 or report.get('trained') != 12
            or report.get('features') != 203 or report.get('limitations') != LIMITATIONS):
        raise ValueError('Report source/count/limitations mismatch')
    validate_identities(report['folds'])
    daily = {}
    for record in report['folds']:
        seed, mode, month = record['seed'], record['mode'], record['month']
        folder = fold_folder(output, seed, mode, month)
        if read_json(folder/'result.json') != record:
            raise ValueError('Fold record/report mismatch')
        rows = read_json(folder/'daily_metrics.json')
        validate_record(record, rows, seed=seed, mode=mode, month=month, source=source,
                        output=output, panel=panel, contracts=contracts, originals=originals,
                        source_daily=source_daily)
        daily[(seed, mode, month)] = rows
    if report['aggregate'] != aggregate_seeds(report['folds'], daily):
        raise ValueError('Independent aggregate mismatch')
    if (output/'SUMMARY.md').read_text(encoding='utf-8') != _summary_text(report):
        raise ValueError('Human summary/report mismatch')
    count = check_old_artifacts(output)
    if report['old_files_checked'] != count:
        raise ValueError('Old artifact audit count mismatch')
    write_json(output/'verification.json', dict(status='PASS', folds=18, reused=6, trained=12,
               features=203, seeds=list(SEEDS), market_dates_per_seed=65, old_files_checked=count,
               changed=[], binding=binding, report_sha256=sha256(output/'report.json')))
    print(f'VERIFIED folds=18 reused=6 trained=12 features=203 dates_per_seed=65 old_files_unchanged={count}', flush=True)


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
