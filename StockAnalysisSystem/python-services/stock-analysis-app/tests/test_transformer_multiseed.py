"""Hand-derived seed statistics; repeated dates are not independent samples."""
from copy import deepcopy
import importlib

import pytest


def api():
    return importlib.import_module('analysis.transformer_multiseed')


def observations():
    records, daily = [], {}
    for seed, value in ((42, 1.0), (123, 2.0), (2026, 3.0)):
        for mode in ('adjusted', 'unadjusted_control'):
            for month, count in (('2026-06', 21), ('2026-07', 23), ('2026-08', 21)):
                records.append(dict(seed=seed, mode=mode, month=month))
                daily[(seed, mode, month)] = [dict(
                    date=f'{month}-{i+1:02}', stocks=20,
                    common_adjusted_label_raw_top5_return=value+(mode == 'adjusted'),
                    common_adjusted_label_raw_excess=value-2+(mode == 'adjusted'),
                    common_adjusted_label_rank_ic=value/10,
                    common_adjusted_label_equal_weight_return=2,
                    common_adjusted_label_momentum_top5_return=0.5,
                    policy_nonnegative_variance_top5_return=value,
                    policy_nonnegative_variance_excess=value-2,
                    policy_legacy_variance_top5_return=value,
                    policy_legacy_variance_excess=value-2,
                ) for i in range(count)]
    return records, daily


def test_statistics_use_three_seed_means_and_sample_sd():
    records, daily = observations()
    report = api().aggregate_seeds(records, daily)
    stats = report['across_seeds']['unadjusted_control']['common_adjusted_label_raw_top5_return']
    assert stats == dict(n_seeds=3, mean=2.0, sample_std=1.0, min=1.0, max=3.0)
    assert report['positive_excess_seeds']['adjusted']['common_adjusted_label_raw_excess'] == 2
    assert report['market_dates_per_seed'] == 65
    assert report['paired_differences']['42']['common_adjusted_label_raw_top5_return'] == 1.0
    assert report['monthly']['123']['adjusted']['2026-07']['days'] == 23


@pytest.mark.parametrize('corruption', ['duplicate', 'missing', 'unknown_seed'])
def test_identity_set_is_exact(corruption):
    records, _ = observations()
    if corruption == 'duplicate':
        records[-1] = deepcopy(records[0])
    elif corruption == 'missing':
        records.pop()
    else:
        records[0]['seed'] = 999
    with pytest.raises(ValueError, match='identity'):
        api().validate_identities(records)


@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), True, '1'])
def test_missing_or_nonfinite_primary_metric_rejected(bad):
    records, daily = observations()
    daily[(42, 'adjusted', '2026-06')][0]['common_adjusted_label_raw_excess'] = bad
    with pytest.raises(ValueError, match='finite'):
        api().aggregate_seeds(records, daily)


def test_seed_date_mismatch_rejected():
    records, daily = observations()
    daily[(123, 'adjusted', '2026-06')][0]['date'] = '2026-06-30'
    with pytest.raises(ValueError, match='dates'):
        api().aggregate_seeds(records, daily)


def test_inconsistent_raw_excess_rejected():
    records, daily = observations()
    daily[(42, 'adjusted', '2026-06')][0]['common_adjusted_label_raw_excess'] = 90
    with pytest.raises(ValueError, match='excess'):
        api().aggregate_seeds(records, daily)


def runner():
    return importlib.import_module('scripts.run_transformer_multiseed')


def test_seed_configs_change_only_seed_and_output_path(tmp_path):
    module = runner()
    a = module.seed_config(tmp_path/'42', {'mode': 'legacy_unadjusted'}, 42)
    b = module.seed_config(tmp_path/'123', {'mode': 'legacy_unadjusted'}, 123)
    assert a['seed'] == 42 and b['seed'] == 123
    assert a['use_probe_selection'] is False
    assert a['score_adjustment_policy'] == 'nonnegative_variance'
    assert {k: v for k, v in a.items() if k not in ('seed', 'output_dir')} == {
        k: v for k, v in b.items() if k not in ('seed', 'output_dir')}


def artifact_record(tmp_path, seed):
    import json
    model = tmp_path/'best_model.pth'
    scaler = tmp_path/'best_model_scaler.pkl'
    metadata = tmp_path/'best_model_preprocessing.json'
    for path, data in ((model, b'checkpoint'), (scaler, b'scaler'), (metadata, b'{}')):
        path.write_bytes(data)
    original = dict(config={'seed': seed}, model_path=str(model), scaler_path=str(scaler),
                    feature_names=['example'])
    (tmp_path/'config.json').write_text(json.dumps(original['config']), encoding='utf-8')
    record = dict(original, seed=seed, origin='reused' if seed == 42 else 'trained',
                  artifact_hashes=runner().artifact_hashes(original))
    return record, original


@pytest.mark.parametrize('bad', ['seed', 'config_seed', 'origin', 'model', 'scaler', 'metadata', 'source_record'])
def test_reuse_provenance_rejects_mislabeled_or_changed_artifacts(tmp_path, bad):
    record, original = artifact_record(tmp_path, 42)
    if bad == 'seed':
        record['seed'] = 123
    elif bad == 'config_seed':
        record['config'] = {'seed': 123}
    elif bad == 'origin':
        record['origin'] = 'trained'
    elif bad == 'source_record':
        record['feature_names'] = ['wrong']
    else:
        name = {'model': 'best_model.pth', 'scaler': 'best_model_scaler.pkl',
                'metadata': 'best_model_preprocessing.json'}[bad]
        (tmp_path/name).write_bytes(b'tampered')
    with pytest.raises(ValueError, match='seed|provenance|hash|reuse'):
        runner().validate_provenance(record, 42, original)


def test_reuse_validation_does_not_write_originals(tmp_path):
    record, original = artifact_record(tmp_path, 42)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    runner().validate_provenance(record, 42, original)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_trained_record_cannot_claim_reuse(tmp_path):
    record, _ = artifact_record(tmp_path, 123)
    runner().validate_provenance(record, 123)
    record['origin'] = 'reused'
    with pytest.raises(ValueError, match='provenance'):
        runner().validate_provenance(record, 123)


def test_partial_folder_preserved(tmp_path):
    folder = tmp_path/'partial'
    folder.mkdir()
    (folder/'evidence').write_bytes(b'keep')
    with pytest.raises(ValueError, match='Partial'):
        runner().prepare_fold(folder)
    assert (folder/'evidence').read_bytes() == b'keep'


def test_complete_folder_is_readable_but_not_recreated(tmp_path):
    import json
    folder = tmp_path/'complete'
    folder.mkdir()
    (folder/'result.json').write_text(json.dumps({'seed': 42}), encoding='utf-8')
    assert runner().prepare_fold(folder) == {'seed': 42}


def test_help_does_not_require_cuda_or_a_provider():
    import subprocess, sys
    from pathlib import Path
    script = Path(__file__).resolve().parents[1]/'scripts/run_transformer_multiseed.py'
    result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--source-experiment' in result.stdout and '--stage' in result.stdout


def test_shared_label_baseline_must_match_between_seeds():
    records, daily = observations()
    row = daily[(123, 'adjusted', '2026-06')][0]
    row['common_adjusted_label_momentum_top5_return'] = 99
    with pytest.raises(ValueError, match='benchmark'):
        api().aggregate_seeds(records, daily)


def test_momentum_rank_can_differ_by_price_mode_but_not_seed():
    records, daily = observations()
    for seed in (42, 123, 2026):
        for row in daily[(seed, 'adjusted', '2026-06')]:
            row['common_adjusted_label_momentum_top5_return'] = 0.7
    result = api().aggregate_seeds(records, daily)
    assert result['monthly']['42']['adjusted']['2026-06']['common_adjusted_label_momentum_top5_return'] == 0.7
    assert result['monthly']['42']['unadjusted_control']['2026-06']['common_adjusted_label_momentum_top5_return'] == 0.5


@pytest.mark.parametrize('seed', [42, 123])
def test_actual_saved_training_config_must_match_record(tmp_path, seed):
    import json
    record, original = artifact_record(tmp_path, seed)
    (tmp_path/'config.json').write_text(json.dumps({'seed': 2026}), encoding='utf-8')
    with pytest.raises(ValueError, match='training config'):
        runner().validate_provenance(record, seed, original if seed == 42 else None)
