import importlib.util
from pathlib import Path
import pytest


def runner():
    path = Path(__file__).resolve().parents[1]/'scripts/run_transformer_fixed_features.py'
    spec = importlib.util.spec_from_file_location('fixed_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_config_disables_probe_and_binds_policy(tmp_path):
    config = runner().fixed_config(tmp_path, {'mode': 'legacy_unadjusted'})
    assert config['use_probe_selection'] is False
    assert config['probe_selected_features_path'] is None
    assert config['score_adjustment_policy'] == 'nonnegative_variance'
    assert (config['num_epochs'], config['batch_size'], config['seed']) == (30, 8, 42)


@pytest.mark.parametrize('bad', ['missing', 'order', 'probe'])
def test_fixed_features_require_exact_full_order(bad):
    from analysis.transformer_features import feature_columns
    record = {'feature_names': feature_columns('158+39'), 'config': {'feature_num': '158+39', 'use_probe_selection': False}}
    if bad == 'missing':
        record['feature_names'] = record['feature_names'][:-1]
    elif bad == 'order':
        record['feature_names'] = record['feature_names'][::-1]
    else:
        record['config']['use_probe_selection'] = True
    with pytest.raises(ValueError, match='feature|probe'):
        runner().validate_fixed_features(record)


def test_output_cannot_share_source_directory(tmp_path):
    with pytest.raises(ValueError, match='Output|output|source'):
        runner().validate_paths(tmp_path, tmp_path)


def test_fold_identity_rejects_duplicate_months():
    module = runner()
    records = [dict(mode=mode, month=month) for mode in module.MODES for month in module.MONTHS]
    records[0] = dict(records[1])
    with pytest.raises(ValueError, match='identity|fold'):
        module.validate_identities(records, module.MONTHS)


def test_help_does_not_train_or_collect():
    import subprocess, sys
    script = Path(__file__).resolve().parents[1]/'scripts/run_transformer_fixed_features.py'
    result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--source-experiment' in result.stdout
    assert '--stage' in result.stdout


def test_fixed_features_reject_actual_sidecar_order():
    from analysis.transformer_features import feature_columns
    columns = feature_columns('158+39')
    record = dict(feature_names=columns, config={'feature_num': '158+39', 'use_probe_selection': False})
    with pytest.raises(ValueError, match='feature|order'):
        runner().validate_fixed_features(record, {'selected_features': columns[::-1]})


def scored_fixture():
    from analysis.transformer_experiment import daily_metrics, summarize
    import numpy as np
    module = runner()
    row = dict(date='2026-06-01', stocks=1,
        **daily_metrics(np.arange(5), np.arange(5), np.zeros(5), np.arange(5)))
    row.update({f'common_adjusted_label_{key}': value for key, value in list(row.items())
                if key not in ('date', 'stocks')})
    for policy in module.POLICIES:
        row.update({f'policy_{policy}_top5_return': 0., f'policy_{policy}_excess': 0.,
                    f'policy_{policy}_top5_changed': 0})
    record = dict(month='2026-06', config={'sequence_length': 1}, summary=summarize([row]))
    return module, record, row


@pytest.mark.parametrize('bad', ['missing_value', 'infinite', 'excess', 'bound_policy'])
def test_scoring_coverage_rejects_invalid_policy_values(monkeypatch, bad):
    from analysis.transformer_experiment import summarize
    module, record, row = scored_fixture()
    monkeypatch.setattr(module, 'validate_fold_metrics', lambda *args: None)
    if bad == 'missing_value':
        row['policy_nonnegative_variance_top5_return'] = None
    elif bad == 'infinite':
        row['policy_nonnegative_variance_top5_return'] = float('inf')
    elif bad == 'excess':
        row['policy_nonnegative_variance_excess'] = 1.
    else:
        row['policy_legacy_variance_top5_return'] = .1
        row['policy_legacy_variance_excess'] = .1
    record['summary'] = summarize([row])
    with pytest.raises(ValueError, match='policy|finite|excess'):
        module.validate_scored_record(record, [row], None)


@pytest.mark.parametrize('bad', ['universe', 'date', 'hash', 'policy', 'order'])
def test_forecast_provenance_rejects_invalid_artifact(tmp_path, bad):
    import pandas as pd
    from analysis.transformer_experiment import sha256
    path = tmp_path/'latest_nonnegative_variance.csv'
    forecast = pd.DataFrame({'排名': range(1, 6), '股票代码': [f'{i:06}.SZ' for i in range(5)],
                             '预测分数': [5., 4., 3., 2., 1.], '调整后分数': [1., .8, .6, .4, .2]})
    if bad == 'universe':
        forecast.loc[0, '股票代码'] = 'invalid'
    elif bad == 'order':
        forecast.loc[0, '调整后分数'] = 0.
    forecast.to_csv(path, index=False)
    metadata = dict(csv_sha256=sha256(path), inference_date='2026-09-30', source_sha256='source',
                    model_sha256='model', score_policy='nonnegative_variance')
    if bad == 'date':
        metadata['inference_date'] = '2026-08-31'
    elif bad == 'hash':
        metadata['csv_sha256'] = 'wrong'
    elif bad == 'policy':
        metadata['score_policy'] = 'legacy_variance'
    with pytest.raises(ValueError, match='forecast|Forecast|provenance|universe'):
        runner().validate_forecast(path, metadata, universe={f'{i:06}.SZ' for i in range(5)},
            inference_date='2026-09-30', source_sha256='source', model_sha256='model',
            policy='nonnegative_variance')
