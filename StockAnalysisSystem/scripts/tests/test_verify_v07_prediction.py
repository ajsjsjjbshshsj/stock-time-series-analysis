"""Independent oracle and adversarial checks; all history here is synthetic."""
import copy
import importlib.util
from pathlib import Path
from decimal import Decimal

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'verify_v07_prediction.py'


def test_verifier_exists():
    assert SCRIPT.exists(), 'V0.7 read-only verifier is not implemented'


@pytest.fixture
def verifier():
    assert SCRIPT.exists(), 'V0.7 read-only verifier is not implemented'
    spec = importlib.util.spec_from_file_location('verify_v07', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample():
    days = ['2024-01-02', '2024-01-03', '2024-01-04', '2024-01-05',
            '2024-01-08', '2024-01-09', '2024-01-10', '2024-01-11',
            '2024-01-12', '2024-01-15']
    source = [dict(trade_date=d, close=Decimal(c)) for d, c in zip(
        days, ['10', '11', '12', '13', '14', '15', '16', '20', '10', '15'])]
    rows = [dict(signal_date=days[7], target_date=days[8], actual_return=-.5, predicted_return=0.),
            dict(signal_date=days[8], target_date=days[9], actual_return=.5, predicted_return=0.)]
    def split(a, b, c, n):
        return dict(signal_start=days[a], signal_end=days[b], label_end=days[c], count=n)
    frozen = dict(schema_version=1, model_id='v07_'+'a'*32, ts_code='000001.SZ',
                  model_type='xgboost', model_name='xgboost_regressor', horizon=1,
                  target='next_trading_day_close_return', data_cutoff=days[-1],
                  source_cutoff=days[-1], seen_through=days[6],
                  splits=dict(train=split(0, 1, 2, 2), val=split(3, 5, 6, 3), test=split(7, 8, 9, 2)),
                  metrics=dict(rmse=.5, mae=.5, r2=0.),
                  baseline_metrics=dict(rmse=.5, mae=.5, r2=0.), test_series=rows,
                  latest=dict(signal_date=days[-1], target_date=None, horizon=1, actual_return=None, predicted_return=.01),
                  feature_importance=[])
    dto = dict(metadata={k: v for k, v in frozen.items() if k not in ('test_series', 'latest', 'feature_importance')},
               test_series=copy.deepcopy(rows), latest=copy.deepcopy(frozen['latest']))
    return source, dto, frozen


def check(verifier, sample):
    source, dto, frozen = sample
    return verifier.verify(source, dto, frozen, '000001.SZ', 'v07_'+'a'*32)


def test_verifies_full_test_metrics_and_bounds(verifier, sample):
    result = check(verifier, sample)
    assert result['status'] == 'PASS'
    assert result['checked_test_rows'] == result['test_count'] == 2
    assert result['source_rows'] == 10
    assert result['training_label_provenance_verified'] is False
    assert result['exchange_calendar_completeness_verified'] is False


def test_reports_long_observed_gap(verifier, sample):
    source, dto, frozen = sample
    source[-1]['trade_date'] = '2024-04-15'
    for payload in (dto['metadata'], frozen):
        payload['data_cutoff'] = payload['source_cutoff'] = '2024-04-15'
        payload['splits']['test']['label_end'] = '2024-04-15'
    dto['latest']['signal_date'] = frozen['latest']['signal_date'] = '2024-04-15'
    dto['test_series'][-1]['target_date'] = frozen['test_series'][-1]['target_date'] = '2024-04-15'
    assert check(verifier, sample)['observed_gaps_over_7_days'] == [
        dict(from_date='2024-01-12', to_date='2024-04-15', calendar_days=94)]


@pytest.mark.parametrize('fault', ['next_date', 'duplicate_test', 'source_duplicate', 'source_unordered',
                                  'cutoff', 'seen', 'nonfinite', 'latest_actual', 'percent',
                                  'baseline', 'truncated', 'frozen', 'bad_close', 'missing_session'])
def test_rejects_faults(verifier, sample, fault):
    source, dto, frozen = sample
    if fault == 'next_date': dto['test_series'][0]['target_date'] = '2024-01-15'
    if fault == 'duplicate_test': dto['test_series'][1]['signal_date'] = dto['test_series'][0]['signal_date']
    if fault == 'source_duplicate': source[1]['trade_date'] = source[0]['trade_date']
    if fault == 'source_unordered': source.reverse()
    if fault == 'cutoff': dto['metadata']['source_cutoff'] = '2025-01-01'
    if fault == 'seen': dto['metadata']['seen_through'] = '2024-01-15'
    if fault == 'nonfinite': dto['metadata']['metrics']['rmse'] = float('nan')
    if fault == 'latest_actual': dto['latest']['actual_return'] = 0
    if fault == 'percent': dto['test_series'][0]['actual_return'] *= 100
    if fault == 'baseline': dto['metadata']['baseline_metrics']['mae'] = .01
    if fault == 'truncated': dto['test_series'].pop()
    if fault == 'frozen': frozen['test_series'][0]['predicted_return'] = .2
    if fault == 'bad_close': source[-1]['close'] = Decimal('Infinity')
    if fault == 'missing_session': source.pop(8)
    with pytest.raises(verifier.VerificationError):
        check(verifier, sample)


def test_checks_metrics_even_if_frozen_and_api_agree_on_wrong_value(verifier, sample):
    sample[1]['metadata']['metrics']['rmse'] = sample[2]['metrics']['rmse'] = .123
    with pytest.raises(verifier.VerificationError, match='metric'):
        check(verifier, sample)


def test_constant_target_r2_is_null(verifier, sample):
    source, dto, frozen = sample
    source[8]['close'] = source[9]['close'] = source[7]['close']
    for row in dto['test_series']: row['actual_return'] = 0.
    frozen['test_series'] = copy.deepcopy(dto['test_series'])
    for group in ('metrics', 'baseline_metrics'):
        dto['metadata'][group] = frozen[group] = dict(rmse=0., mae=0., r2=None)
    assert check(verifier, sample)['status'] == 'PASS'


def test_http_failure_is_sanitized(verifier, monkeypatch):
    def fail(*a, **k): raise RuntimeError('secret-password user@db')
    monkeypatch.setattr(verifier, 'urlopen', fail)
    with pytest.raises(verifier.VerificationError) as caught:
        verifier.fetch('http://127.0.0.1:8084', '/health', 5)
    assert 'secret-password' not in str(caught.value)


def test_cli_failure_is_sanitized(verifier, monkeypatch, capsys):
    def fail(*a, **k): raise RuntimeError('secret-password user@db')
    monkeypatch.setattr(verifier, 'run', fail)
    assert verifier.main(['--stock', '000001.SZ', '--model', 'v07_'+'a'*32,
                          '--start', '2024-01-02', '--end', '2024-01-15']) == 1
    assert 'secret-password' not in capsys.readouterr().err


def test_duplicate_json_keys_rejected(verifier):
    with pytest.raises(verifier.VerificationError):
        verifier.read_json('{"a": 1, "a": 2}')


def test_rejects_jointly_truncated_frozen_and_api_test(verifier, sample):
    source, dto, frozen = sample
    # Dropping the first signal from both sides must not turn partial validation into PASS.
    dto['test_series'].pop(0)
    frozen['test_series'].pop(0)
    for metadata in (dto['metadata'], frozen):
        metadata['splits']['test']['count'] = 1
    with pytest.raises(verifier.VerificationError):
        check(verifier, sample)


def test_rejects_boolean_horizon(verifier, sample):
    sample[1]['metadata']['horizon'] = sample[2]['horizon'] = True
    with pytest.raises(verifier.VerificationError):
        check(verifier, sample)
