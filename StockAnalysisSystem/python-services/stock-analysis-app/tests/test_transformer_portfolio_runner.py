"""Offline audit and three-seed summaries, without live providers or training."""
import importlib
import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('scripts.run_transformer_portfolio_backtest')


def records():
    result = []
    for seed, value in ((42, .1), (123, .2), (2026, .3)):
        for mode in ('adjusted', 'unadjusted_control'):
            for policy in ('raw', 'nonnegative_variance'):
                for scenario in ('gross', 'fee3', 'fee3_slip5', 'fee3_slip10'):
                    result.append(dict(seed=seed, mode=mode, policy=policy, scenario=scenario,
                                       metrics=dict(total_return=value, max_drawdown=-value,
                                                    total_fees=1., summed_one_sided_turnover=2.),
                                       benchmark_excess=value-.2))
    return result


def test_seed_statistics_count_three_not_48_or_market_dates():
    result = api().aggregate_results(records())
    row = result['adjusted/raw/gross']
    assert row['total_return']['n_seeds'] == 3
    assert row['total_return']['mean'] == pytest.approx(.2)
    assert row['total_return']['sample_std'] == pytest.approx(.1)
    assert row['positive_excess_seeds'] == 1


@pytest.mark.parametrize('bad', ['duplicate', 'missing', 'wrong_seed', 'nonfinite'])
def test_exact_48_finite_results_required(bad):
    values = records()
    if bad == 'duplicate':
        values[-1] = dict(values[0])
    elif bad == 'missing':
        values.pop()
    elif bad == 'wrong_seed':
        values[0]['seed'] = 999
    else:
        values[0]['metrics']['total_return'] = np.nan
    with pytest.raises(ValueError, match='identity|finite'):
        api().aggregate_results(values)


def scores_fixture():
    dates = pd.bdate_range('2026-06-01', '2026-07-08')
    codes = [f'{index:06}.SZ' for index in range(20)]
    prices = pd.DataFrame([dict(ts_code=code, trade_date=date,
                               open=10+stock+(day*.1)*(1+stock/20), close=10+stock+(day*.1)*(1+stock/20))
                           for day, date in enumerate(dates) for stock, code in enumerate(codes)])
    record = dict(seed=42, mode='adjusted', month='2026-06', artifact_hashes={'model': 'f'*64})
    rows = [dict(date=str(date.date()), ts_code=code, raw=20-stock, nonnegative_variance=20-stock,
                 model_sha256='f'*64) for date in dates if date.month == 6 for stock, code in enumerate(codes)]
    expected = []
    for day, date in enumerate(dates):
        if date.month != 6:
            continue
        top = np.mean([(10+stock+((day+5)*.1)*(1+stock/20))/(10+stock+((day+1)*.1)*(1+stock/20))-1
                       for stock in range(5)])
        expected.append(dict(date=str(date.date()), common_adjusted_label_raw_top5_return=top,
                             policy_nonnegative_variance_top5_return=top))
    return record, rows, prices, expected


@pytest.mark.parametrize('bad', ['missing_date', 'duplicate', 'unknown_stock', 'wrong_model', 'nonfinite', 'wrong_top5'])
def test_signal_provenance_and_historical_top5_agreement_rejected_when_invalid(bad):
    record, rows, panel, expected = scores_fixture()
    if bad == 'missing_date':
        rows = rows[20:]
    elif bad == 'duplicate':
        rows.append(dict(rows[0]))
    elif bad == 'unknown_stock':
        rows[0]['ts_code'] = 'unknown'
    elif bad == 'wrong_model':
        rows[0]['model_sha256'] = 'x'*64
    elif bad == 'nonfinite':
        rows[0]['raw'] = None
    else:
        rows[19]['raw'] = 999.
    with pytest.raises(ValueError, match='[Ss]ignal|Top5'):
        api().validate_signal_rows(rows, record, panel, panel, expected)


def test_valid_frozen_scores_agree_with_real_price_top5():
    record, rows, panel, expected = scores_fixture()
    api().validate_signal_rows(rows, record, panel, panel, expected)


def test_resume_rejects_wrong_identity_before_loading_scores(tmp_path):
    from analysis.transformer_experiment import write_json, sha256
    record, rows, panel, expected = scores_fixture()
    write_json(tmp_path/'scores.json', rows)
    manifest = dict(identity={'seed': 123}, scores_sha256=sha256(tmp_path/'scores.json'))
    with pytest.raises(ValueError, match='identity'):
        api().read_scores(tmp_path, manifest, {'seed': 42}, record, panel, panel, expected)


def test_resume_rejects_tampered_scores_bytes(tmp_path):
    from analysis.transformer_experiment import write_json, sha256
    record, rows, panel, expected = scores_fixture()
    write_json(tmp_path/'scores.json', rows)
    manifest = dict(identity={'seed': 42}, scores_sha256=sha256(tmp_path/'scores.json'))
    rows[0]['raw'] = 100
    write_json(tmp_path/'scores.json', rows)
    with pytest.raises(ValueError, match='hash'):
        api().read_scores(tmp_path, manifest, {'seed': 42}, record, panel, panel, expected)


def test_output_cannot_overlap_source(tmp_path):
    with pytest.raises(ValueError, match='Output'):
        api().validate_paths(tmp_path, tmp_path)


def test_help_does_not_run_training_or_collect_data():
    import subprocess, sys
    script = Path(__file__).resolve().parents[1]/'scripts/run_transformer_portfolio_backtest.py'
    result = subprocess.run([sys.executable, str(script), '--help'], capture_output=True, text=True)
    assert result.returncode == 0
    assert '--stage' in result.stdout and '--initial-capital' in result.stdout


def test_numpy_real_metrics_preserve_numeric_counts_through_report_json(tmp_path):
    from analysis.transformer_experiment import write_json
    values = records()
    for row in values:
        row['benchmark_excess'] = np.float64(row['benchmark_excess'])
    aggregate = api().aggregate_results(values)
    write_json(tmp_path/'report.json', aggregate)
    restored = json.loads((tmp_path/'report.json').read_text(encoding='utf-8'))
    assert restored['adjusted/raw/gross']['positive_excess_seeds'] == 1
    assert restored == aggregate
