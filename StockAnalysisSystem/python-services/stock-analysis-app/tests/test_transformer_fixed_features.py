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
