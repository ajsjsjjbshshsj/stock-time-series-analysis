"""Seed sensitivity, not a market-sample significance test.

Average the identical 65 mature dates *within* each seed first. Standard
deviation then uses three seed means (ddof=1), never 195 seed/date rows.
"""
import math
from numbers import Real
from statistics import mean, stdev

SEEDS = (42, 123, 2026)
MODES = ('adjusted', 'unadjusted_control')
MONTHS = ('2026-06', '2026-07', '2026-08')
MONTH_DAYS = {'2026-06': 21, '2026-07': 23, '2026-08': 21}
METRICS = (
    'common_adjusted_label_raw_top5_return',
    'common_adjusted_label_raw_excess',
    'common_adjusted_label_rank_ic',
    'common_adjusted_label_equal_weight_return',
    'common_adjusted_label_momentum_top5_return',
    'policy_nonnegative_variance_top5_return',
    'policy_nonnegative_variance_excess',
    'policy_legacy_variance_top5_return',
    'policy_legacy_variance_excess',
)
EXCESS_METRICS = tuple(key for key in METRICS if key.endswith('excess'))


def validate_identities(records):
    identities = [(r['seed'], r['mode'], r['month']) for r in records]
    expected = {(seed, mode, month) for seed in SEEDS for mode in MODES for month in MONTHS}
    if len(identities) != 18 or set(identities) != expected:
        raise ValueError('Duplicate or incomplete seed/mode/month identity set')


def validate_rows(rows, month):
    dates = [r['date'] for r in rows]
    if (len(dates) != MONTH_DAYS[month] or len(set(dates)) != len(dates)
            or dates != sorted(dates) or any(not d.startswith(month+'-') for d in dates)):
        raise ValueError('Incomplete, duplicate or unordered month dates')
    for row in rows:
        if row.get('stocks') != 20:
            raise ValueError('Incomplete stock universe')
        if any(not isinstance(row.get(key), Real) or isinstance(row[key], bool)
               or not math.isfinite(row[key]) for key in METRICS):
            raise ValueError('Required metrics must be finite numeric values')
        for return_key, excess_key in (
            ('common_adjusted_label_raw_top5_return', 'common_adjusted_label_raw_excess'),
            ('policy_nonnegative_variance_top5_return', 'policy_nonnegative_variance_excess'),
            ('policy_legacy_variance_top5_return', 'policy_legacy_variance_excess'),
        ):
            expected = row[return_key]-row['common_adjusted_label_equal_weight_return']
            if not math.isclose(row[excess_key], expected, rel_tol=1e-7, abs_tol=1e-9):
                raise ValueError('Inconsistent common-label excess')


def _averages(rows):
    return dict(days=len(rows), **{key: mean(row[key] for row in rows) for key in METRICS})


def _statistics(values):
    return dict(n_seeds=3, mean=mean(values), sample_std=stdev(values), min=min(values), max=max(values))


def aggregate_seeds(records, daily):
    validate_identities(records)
    expected = {(r['seed'], r['mode'], r['month']) for r in records}
    if set(daily) != expected:
        raise ValueError('Daily metric identity set mismatch')
    reference, benchmarks, equal_weight = {}, {}, {}
    monthly, per_seed = {}, {}
    for seed in SEEDS:
        monthly[str(seed)], per_seed[str(seed)] = {}, {}
        for mode in MODES:
            monthly[str(seed)][mode] = {}
            combined = []
            for month in MONTHS:
                rows = daily[(seed, mode, month)]
                validate_rows(rows, month)
                dates = [row['date'] for row in rows]
                if month in reference and reference[month] != dates:
                    raise ValueError('Seed/mode dates mismatch')
                reference[month] = dates
                equal = [row['common_adjusted_label_equal_weight_return'] for row in rows]
                if month in equal_weight and equal_weight[month] != equal:
                    raise ValueError('Common-label equal-weight benchmark mismatch between seeds/modes')
                equal_weight[month] = equal
                # Momentum ranks use each mode's price history; labels remain adjusted.
                baseline = [row['common_adjusted_label_momentum_top5_return'] for row in rows]
                benchmark_key = (mode, month)
                if benchmark_key in benchmarks and benchmarks[benchmark_key] != baseline:
                    raise ValueError('Common-label momentum benchmark mismatch between seeds')
                benchmarks[benchmark_key] = baseline
                monthly[str(seed)][mode][month] = _averages(rows)
                combined.extend(rows)
            per_seed[str(seed)][mode] = _averages(combined)
    across = {mode: {key: _statistics([per_seed[str(s)][mode][key] for s in SEEDS])
                     for key in METRICS} for mode in MODES}
    positive = {mode: {key: sum(per_seed[str(s)][mode][key] > 0 for s in SEEDS)
                       for key in EXCESS_METRICS} for mode in MODES}
    paired = {str(s): {key: per_seed[str(s)]['adjusted'][key]
                           - per_seed[str(s)]['unadjusted_control'][key]
                      for key in METRICS} for s in SEEDS}
    paired_monthly = {str(s): {m: {key: monthly[str(s)]['adjusted'][m][key]
                                     - monthly[str(s)]['unadjusted_control'][m][key]
                                 for key in METRICS} for m in MONTHS} for s in SEEDS}
    return dict(statistical_unit='seed mean over identical market dates', n_seeds=3,
                market_dates_per_seed=65, per_seed=per_seed, monthly=monthly,
                across_seeds=across, positive_excess_seeds=positive,
                paired_differences=paired, paired_monthly_differences=paired_monthly,
                paired_across_seeds={key: _statistics([paired[str(s)][key] for s in SEEDS]) for key in METRICS})
