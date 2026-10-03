import copy
import json
import pytest
import subprocess
import sys
from tests.test_evaluation_protocol import history


@pytest.fixture(scope='module')
def report():
    from analysis.evaluation.training import evaluate
    frame, calendar = history()
    return evaluate(frame, '000001.SZ', calendar)


def test_roundtrip_and_summary(report):
    from analysis.evaluation.contracts import parse_report, make_summary, parse_summary
    assert parse_report(json.dumps(report).encode()) == report
    summary = make_summary(report, 'a'*64, 1234)
    assert parse_summary(json.dumps(summary)) == summary
    assert summary['evaluation']['count'] == 300


@pytest.mark.parametrize('field,value', [('protocol_id','bad'), ('history_previously_observed',1),
    ('prospective_validation',True), ('units','percent'), ('horizon',True), ('feature_names',[]),
    ('methods',{}), ('schema_version',True)])
def test_reject_tampering(report, field, value):
    from analysis.evaluation.contracts import validate_report
    bad = copy.deepcopy(report); bad[field] = value
    with pytest.raises(ValueError): validate_report(bad)


def test_reject_series_and_metrics(report):
    from analysis.evaluation.contracts import validate_report, parse_report
    for mutate in (lambda r: r['series'].pop(),
                   lambda r: r['series'][0].update(target_date=r['series'][0]['signal_date']),
                   lambda r: r['overall_metrics']['ridge'].update(rmse=float('nan')),
                   lambda r: r['series'][0]['predicted_returns'].update(zero_return=.1)):
        bad = copy.deepcopy(report); mutate(bad)
        with pytest.raises(ValueError): validate_report(bad)
    for raw in (b'{"a":1,"a":2}', b'{"a":NaN}'):
        with pytest.raises(ValueError): parse_report(raw)


def test_pooled_metrics(report):
    from analysis.evaluation.training import metrics
    for method in report['methods']:
        assert report['overall_metrics'][method] == metrics(
            [r['actual_return'] for r in report['series']],
            [r['predicted_returns'][method] for r in report['series']])
    assert report['overall_metrics']['zero_return']['rmse'] != sum(
        f['metrics']['zero_return']['rmse'] for f in report['folds'])/3


def test_contract_import_is_lightweight():
    result = subprocess.run([sys.executable, '-c',
        "import sys; import analysis.evaluation.contracts; assert 'xgboost' not in sys.modules; assert 'sklearn' not in sys.modules; assert 'analysis.evaluation.training' not in sys.modules"],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mutate', [
    lambda r: r['source_continuity'].update(extra=True),
    lambda r: r.update(feature_names=tuple(r['feature_names'])),
])
def test_strict_container_contract(report, mutate):
    from analysis.evaluation.contracts import validate_report
    bad = copy.deepcopy(report); mutate(bad)
    with pytest.raises(ValueError): validate_report(bad)
