"""Audited offline price-driven portfolio replay; no training or provider requests."""
import argparse
import hashlib
import json
import math
from numbers import Real
import os
from pathlib import Path
from statistics import mean, stdev
import sys

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
os.environ.setdefault('MPLBACKEND', 'Agg')
os.environ.setdefault('OMP_NUM_THREADS', '8')

import numpy as np
import pandas as pd
from analysis.portfolio_backtest import POLICIES, SCENARIOS, make_schedule, run_portfolio
from analysis.transformer_multiseed import SEEDS, MODES, MONTHS, aggregate_seeds, validate_identities
from analysis.transformer_experiment import sha256, write_json
from analysis.transformer_portfolio_signals import infer_month
from scripts.run_transformer_fixed_features import bind_output, check_old_artifacts, read_json
from scripts.run_transformer_multiseed import source_inputs, validate_record, fold_folder, prepare_fold

START, END = '2026-06-01', '2026-08-31'
LIMITATIONS = [
    'Known historical diagnostic period, including September tail exits; not a new blind holdout or profit promise.',
    'Fractional adjusted asset units proxy returns; not a raw-share/cash-dividend/board-lot account.',
    'Ideal quoted-open fills; no suspension/limit/queue/capacity/live execution simulation.',
    'Costs are hypothetical aggregate bps, not legal tax rates or broker quotes; no minimum commission rules.',
    'Fixed20 pool selection bias and revised historical data; three seeds are not independent markets.',
    'Raw ranking primary, nonnegative_variance secondary; all seeds and cost scenarios retained.',
    'Fully sell then buy every four session intervals; retained names also incur both sides of costs.',
]


def digest_json(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def parameters(initial_capital):
    if (not isinstance(initial_capital, Real) or isinstance(initial_capital, bool)
            or not np.isfinite(initial_capital) or initial_capital <= 0):
        raise ValueError('Invalid initial capital')
    return dict(version='cash_ledger_v1', initial_capital=float(initial_capital), start_date=START,
                end_signal_date=END, top_k=5, entry_offset=1, exit_offset=5, signal_step=4,
                policies=list(POLICIES), scenarios=list(SCENARIOS), tie_break='ts_code_ascending')


def validate_paths(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if (output.parent != APP/'models/transformer' or not output.name.startswith('portfolio_backtest_')
            or source.is_relative_to(output) or output.is_relative_to(source)):
        raise ValueError('Output must be separate models/transformer/portfolio_backtest_*')
    return source, output


def load_experiment(source, initial_capital):
    """Validate the original multiseed experiment WITHOUT its writing verify()."""
    report = read_json(source/'report.json')
    fixed = Path(report['binding']['source'])
    panel, panels, contracts, originals, source_daily, fixed_binding = source_inputs(fixed)
    if report['binding'] != fixed_binding or read_json(source/'source_identity.json') != fixed_binding:
        raise ValueError('Source multiseed binding mismatch')
    if (report.get('reused'), report.get('trained'), report.get('features')) != (6, 12, 203):
        raise ValueError('Source multiseed count mismatch')
    validate_identities(report['folds'])
    daily, records = {}, {}
    for record in report['folds']:
        seed, mode, month = record['seed'], record['mode'], record['month']
        folder = fold_folder(source, seed, mode, month)
        if read_json(folder/'result.json') != record:
            raise ValueError('Source fold/report mismatch')
        rows = read_json(folder/'daily_metrics.json')
        validate_record(record, rows, seed=seed, mode=mode, month=month, source=fixed,
                        output=source, panel=panel, contracts=contracts, originals=originals, source_daily=source_daily)
        records[(seed, mode, month)], daily[(seed, mode, month)] = record, rows
    if aggregate_seeds(report['folds'], daily) != report['aggregate']:
        raise ValueError('Source seed aggregate mismatch')
    verification = read_json(source/'verification.json')
    if (verification.get('status') != 'PASS' or verification.get('report_sha256') != sha256(source/'report.json')
            or verification.get('binding') != fixed_binding or verification.get('changed') != []):
        raise ValueError('Source verified report hash mismatch')
    check_old_artifacts(source)
    binding = dict(source=str(source), report_sha256=sha256(source/'report.json'),
                   verification_sha256=sha256(source/'verification.json'), fixed_binding=fixed_binding,
                   parameters=parameters(initial_capital),
                   model_identities=[signal_identity(record) for record in report['folds']])
    return panel, panels, records, daily, binding


def signal_identity(record):
    return dict(seed=record['seed'], mode=record['mode'], month=record['month'],
                artifact_hashes=record['artifact_hashes'], config_sha256=digest_json(record['config']),
                train_end=record['train_end'], feature_names=record['feature_names'])


def validate_signal_rows(rows, record, panel, adjusted, expected_daily):
    """Bind scores to model/universe/date, and cross-check original historical Top5 returns."""
    required = {'date', 'ts_code', 'raw', 'nonnegative_variance', 'model_sha256'}
    month = record['month']
    expected_dates = sorted({str(pd.Timestamp(date).date()) for date in panel.trade_date
                             if str(pd.Timestamp(date).date()).startswith(month)})
    codes = set(panel.ts_code)
    identities = [(row.get('date'), row.get('ts_code')) for row in rows]
    expected = [(date, code) for date in expected_dates for code in sorted(codes)]
    if identities != expected or any(set(row) != required for row in rows):
        raise ValueError('Incomplete/duplicate/unordered signal date/universe coverage')
    for row in rows:
        if row['model_sha256'] != record['artifact_hashes']['model']:
            raise ValueError('Signal model hash mismatch')
        if any(not isinstance(row[key], Real) or isinstance(row[key], bool) or not math.isfinite(row[key])
               for key in POLICIES):
            raise ValueError('Signal scores must be finite numeric values')
    truth = adjusted.sort_values(['ts_code', 'trade_date']).copy()
    grouped = truth.groupby('ts_code').open
    truth['return'] = grouped.shift(-5)/grouped.shift(-1)-1
    truth = truth.set_index(['trade_date', 'ts_code'])['return']
    by_date = {row['date']: row for row in expected_daily}
    frame = pd.DataFrame(rows)
    for date in expected_dates:
        if date not in by_date:
            raise ValueError('Signal historical Top5 date coverage mismatch')
        day = frame[frame.date == date]
        for policy, key in [('raw', 'common_adjusted_label_raw_top5_return'),
                            ('nonnegative_variance', 'policy_nonnegative_variance_top5_return')]:
            chosen = day.sort_values([policy, 'ts_code'], ascending=[False, True]).head(5).ts_code
            realized = np.array([truth.loc[(pd.Timestamp(date), code)] for code in chosen], dtype=float)
            if not np.isfinite(realized).all() or not np.isclose(realized.mean(), by_date[date][key],
                                                                rtol=1e-6, atol=1e-8):
                raise ValueError(f'Signal historical Top5 mismatch: {date} {policy}')


def read_scores(folder, manifest, identity, record, panel, adjusted, expected_daily):
    if manifest.get('identity') != identity:
        raise ValueError('Signal resume identity mismatch')
    if manifest.get('scores_sha256') != sha256(folder/'scores.json'):
        raise ValueError('Signal resume hash mismatch')
    rows = read_json(folder/'scores.json')
    validate_signal_rows(rows, record, panel, adjusted, expected_daily)
    return rows


def signal_folder(output, seed, mode, month):
    return output/'signals'/f'seed_{seed}'/mode/month


def collect_scores(output, panel, panels, records, daily, *, generate):
    collected = {}
    for seed in SEEDS:
        for mode in MODES:
            joined = []
            for month in MONTHS:
                record = records[(seed, mode, month)]
                folder = signal_folder(output, seed, mode, month)
                identity = signal_identity(record)
                if generate:
                    manifest = prepare_fold(folder)
                else:
                    manifest = read_json(folder/'result.json')
                if manifest is None:
                    print(f'SIGNAL_START seed={seed} mode={mode} month={month}', flush=True)
                    rows = infer_month(panels[mode], record, month)
                    validate_signal_rows(rows, record, panel, panels['adjusted'], daily[(seed, mode, month)])
                    write_json(folder/'scores.json', rows)
                    manifest = dict(identity=identity, scores_sha256=sha256(folder/'scores.json'))
                    write_json(folder/'result.json', manifest)
                rows = read_scores(folder, manifest, identity, record, panel, panels['adjusted'], daily[(seed, mode, month)])
                joined.extend(rows)
                print(f'SIGNAL_COMPLETE seed={seed} mode={mode} month={month}', flush=True)
            collected[(seed, mode)] = pd.DataFrame(joined)
    return collected


def config_for(scenario, capital, policy):
    return dict(initial_capital=float(capital), policy=policy,
                **{key: scenario[key] for key in ('buy_fee_bps', 'sell_fee_bps', 'slippage_bps')})


def benchmark_scores(prices):
    schedule = make_schedule(prices, START, END)
    return pd.DataFrame([dict(date=cycle['signal_date'], ts_code=code, raw=0., nonnegative_variance=0.)
                         for cycle in schedule for code in sorted(prices.ts_code.unique())])


def replay_benchmarks(output, prices, capital, *, generate):
    scores, result = benchmark_scores(prices), {}
    for scenario in SCENARIOS:
        folder = output/'benchmarks'/scenario['id']
        saved = prepare_fold(folder) if generate else read_json(folder/'result.json')
        expected = run_portfolio(prices, scores, config_for(scenario, capital, 'raw'), START, END,
                                 top_k=prices.ts_code.nunique())
        if saved is not None and saved != expected:
            raise ValueError('Same-cost benchmark resume/recomputation mismatch')
        if saved is None:
            write_json(folder/'result.json', expected)
        result[scenario['id']] = expected
    return result


def result_folder(output, seed, mode, policy, scenario):
    return output/'portfolios'/f'seed_{seed}'/mode/policy/scenario


def replay_results(output, prices, scores, benchmarks, capital, *, generate):
    records = []
    for seed in SEEDS:
        for mode in MODES:
            for policy in POLICIES:
                for scenario in SCENARIOS:
                    identity = dict(seed=seed, mode=mode, policy=policy, scenario=scenario['id'])
                    folder = result_folder(output, seed, mode, policy, scenario['id'])
                    saved = prepare_fold(folder) if generate else read_json(folder/'result.json')
                    expected = dict(identity=identity, portfolio=run_portfolio(prices, scores[(seed, mode)],
                                    config_for(scenario, capital, policy), START, END))
                    if saved is not None and saved != expected:
                        raise ValueError('Portfolio resume ledger/parameter/recomputation mismatch')
                    if saved is None:
                        write_json(folder/'result.json', expected)
                    portfolio, benchmark = expected['portfolio'], benchmarks[scenario['id']]
                    record = dict(identity, metrics=portfolio['metrics'], monthly=portfolio['monthly'],
                                  benchmark_excess=portfolio['metrics']['total_return']-benchmark['metrics']['total_return'],
                                  monthly_benchmark_excess={month: value['return']-benchmark['monthly'][month]['return']
                                                           for month, value in portfolio['monthly'].items()},
                                  result_sha256=sha256(folder/'result.json'))
                    records.append(record)
    return records


def aggregate_results(records):
    identities = [(r['seed'], r['mode'], r['policy'], r['scenario']) for r in records]
    expected = {(s, m, p, c['id']) for s in SEEDS for m in MODES for p in POLICIES for c in SCENARIOS}
    if len(identities) != 48 or set(identities) != expected:
        raise ValueError('Duplicate/incomplete portfolio identity set')
    metrics = ('total_return', 'max_drawdown', 'total_fees', 'summed_one_sided_turnover', 'benchmark_excess')
    result = {}
    for mode in MODES:
        for policy in POLICIES:
            for scenario in SCENARIOS:
                group = [r for r in records if (r['mode'], r['policy'], r['scenario']) == (mode, policy, scenario['id'])]
                summary = {}
                for key in metrics:
                    values = [r[key] if key == 'benchmark_excess' else r['metrics'][key] for r in group]
                    if any(not isinstance(v, Real) or isinstance(v, bool) or not np.isfinite(v) for v in values):
                        raise ValueError('Portfolio statistics require finite numeric values')
                    summary[key] = dict(n_seeds=3, mean=mean(values), sample_std=stdev(values),
                                        min=min(values), max=max(values))
                summary['positive_excess_seeds'] = sum(bool(r['benchmark_excess'] > 0) for r in group)
                result[f'{mode}/{policy}/{scenario["id"]}'] = summary
    return result


def summary_text(report):
    lines = ['# 行情驱动非重叠资金回测', '',
             '18个冻结模型，48个策略/成本组合；无重新训练。',
             'T收盘信号→T+1开盘买入→T+5开盘卖出，每4交易间隔全卖再买，跨月不断档。',
             '初始本金：'+str(report['binding']['parameters']['initial_capital'])+'；理想成交、复权可分割资产单位。',
             '费用/滑点为假设，不是实际税率或券商报价。6—8月信号及9月尾仓为历史诊断，非盲测。', '',
             '| 模式 | 排序 | 费用情景 | 3seed累计收益均值 | 样本SD | 最大回撤均值 | 超额均值 | 正超额seed |',
             '|---|---|---|---:|---:|---:|---:|---:|']
    for identity, row in report['aggregate'].items():
        mode, policy, scenario = identity.split('/')
        lines.append(f'| {mode} | {policy} | {scenario} | {row["total_return"]["mean"]:.4%} | '
                     f'{row["total_return"]["sample_std"]:.4%} | {row["max_drawdown"]["mean"]:.4%} | '
                     f'{row["benchmark_excess"]["mean"]:.4%} | {row["positive_excess_seeds"]}/3 |')
    lines += ['', '每日净值和逐笔账本见 portfolios/seed_*/模式/排序/费用/result.json；',
              '逐seed及逐月结果见report.json，信号分数见signals/，同成本基准见benchmarks/。', '',
              '## 局限', '', *['- '+item for item in LIMITATIONS]]
    return '\n'.join(lines)+'\n'


def run(source, output, initial_capital):
    panel, panels, models, daily, binding = load_experiment(source, initial_capital)
    bind_output(output, binding, initialize=True)
    check_old_artifacts(output)
    if (output/'report.json').exists():
        verify(source, output, initial_capital)
        print('COMPLETE_RESUME no new scores/fits/writes to completed ledgers', flush=True)
        return
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError('Fresh frozen score generation requires approved CUDA')
    torch.set_num_threads(min(8, os.cpu_count() or 1))
    scores = collect_scores(output, panel, panels, models, daily, generate=True)
    prices = panels['adjusted']
    benchmarks = replay_benchmarks(output, prices, initial_capital, generate=True)
    records = replay_results(output, prices, scores, benchmarks, initial_capital, generate=True)
    report = dict(binding=binding, gpu=torch.cuda.get_device_name(0), results=records,
                  aggregate=aggregate_results(records), benchmarks={key: value['metrics'] for key, value in benchmarks.items()},
                  limitations=LIMITATIONS, old_files_checked=check_old_artifacts(output))
    write_json(output/'report.json', report)
    (output/'SUMMARY.md').write_text(summary_text(report), encoding='utf-8')
    verify(source, output, initial_capital)


def verify(source, output, initial_capital):
    panel, panels, models, daily, binding = load_experiment(source, initial_capital)
    bind_output(output, binding)
    report = read_json(output/'report.json')
    if report['binding'] != binding or report.get('limitations') != LIMITATIONS:
        raise ValueError('Report source/parameters/limitations mismatch')
    scores = collect_scores(output, panel, panels, models, daily, generate=False)
    benchmarks = replay_benchmarks(output, panels['adjusted'], initial_capital, generate=False)
    records = replay_results(output, panels['adjusted'], scores, benchmarks, initial_capital, generate=False)
    if (report['results'] != records or report['aggregate'] != aggregate_results(records)
            or report['benchmarks'] != {key: value['metrics'] for key, value in benchmarks.items()}):
        raise ValueError('Independent portfolio report recomputation mismatch')
    if (output/'SUMMARY.md').read_text(encoding='utf-8') != summary_text(report):
        raise ValueError('Human portfolio summary mismatch')
    count = check_old_artifacts(output)
    if count != report['old_files_checked']:
        raise ValueError('Portfolio old-file count mismatch')
    write_json(output/'verification.json', dict(status='PASS', signal_models=18, portfolios=48,
               benchmarks=4, no_new_training=True, old_files_checked=count, changed=[],
               report_sha256=sha256(output/'report.json'), binding=binding))
    print(f'VERIFIED signal_models=18 portfolios=48 benchmarks=4 old_files_unchanged={count}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('run', 'verify'), required=True)
    parser.add_argument('--source-experiment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initial-capital', type=float, default=1000000.)
    args = parser.parse_args()
    source, output = validate_paths(args.source_experiment, args.output)
    {'run': run, 'verify': verify}[args.stage](source, output, args.initial_capital)


if __name__ == '__main__':
    main()
