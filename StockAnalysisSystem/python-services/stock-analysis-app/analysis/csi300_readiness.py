"""Conservative data-coverage preflight, never training or deployment qualification."""
from datetime import datetime, time
import numpy as np
import pandas as pd
from analysis.csi300_universe import identity, iso_date
from analysis.strategy_dates import validate_session_calendar, SHANGHAI

DAILY = ['open', 'high', 'low', 'close', 'vol', 'amount']
VALUATION = ['turnover_rate', 'pe', 'pe_ttm', 'pb', 'ps', 'total_mv']
PROFILES = dict(single_xgb=dict(required=DAILY, warmup=60, sequence=1, mature=60, horizon=1),
                lightgbm=dict(required=DAILY+VALUATION, warmup=120, sequence=1, mature=365, horizon=5),
                transformer=dict(required=DAILY, warmup=253, sequence=60, mature=60, horizon=5))


def validate_pool(pool):
    codes = pool.get('codes', [])
    if (len(codes) != 300 or codes != sorted(set(codes))
            or pool.get('stockid2idx') != {c:i for i,c in enumerate(codes)}
            or pool.get('survivorship_bias') is not True):
        raise ValueError('Fixed300 pool identity required')
    return codes


def normalize(frame, date_column='trade_date'):
    if frame is None: return None
    result = frame.copy()
    if result.columns.duplicated().any(): raise ValueError('Duplicate source columns')
    if date_column in result:
        result[date_column] = pd.to_datetime(result[date_column], errors='coerce')
        if result[date_column].dt.tz is not None: raise ValueError('Source dates must be date-only')
    return result


def frame_identity(frame):
    if frame is None: return None
    result = frame.copy()
    for field in ('trade_date', 'list_date'):
        if field in result: result[field] = pd.to_datetime(result[field], errors='coerce')
    keys = [k for k in ('ts_code', 'trade_date') if k in result]
    if keys: result = result.sort_values(keys)
    import json
    return identity(json.loads(result[sorted(result.columns)].to_json(orient='records', date_format='iso', double_precision=15)))


def _valid(frame, fields):
    if not set(fields).issubset(frame): return False
    for field in fields:
        values = frame[field]
        if values.map(lambda v:isinstance(v, (bool, np.bool_))).any(): return False
        values = pd.to_numeric(values, errors='coerce')
        if not np.isfinite(values).all(): return False
        if field in ('open', 'high', 'low', 'close', 'adj_factor') and (values <= 0).any(): return False
        if field in ('vol', 'amount', 'turnover_rate', 'total_mv') and (values < 0).any(): return False
    if {'open', 'high', 'low', 'close'}.issubset(fields):
        numeric = frame[['open', 'high', 'low', 'close']].apply(pd.to_numeric)
        if ((numeric.high < numeric[['open','close','low']].max(axis=1))
                | (numeric.low > numeric[['open','close','high']].min(axis=1))).any(): return False
    return True


def diagnose_readiness(universe, daily, basic, calendar, *, factors=None, factor_verified=False,
                       start_date, end_date, selected_stock, request_time, source_identity):
    codes = validate_pool(universe)
    if selected_stock not in codes: raise ValueError('Selected stock outside frozen pool')
    if not isinstance(request_time, datetime) or request_time.utcoffset() is None:
        raise ValueError('Aware request timestamp required')
    now = request_time.astimezone(SHANGHAI)
    iso_date(start_date)
    report = dict(schema_version=1, profile_version=1, pool_version=universe['pool_version'],
        codes_sha256=identity(codes), survivorship_bias=True, request_time=now.isoformat(),
        requested_start=start_date, target_trade_date=None, status='NOT_READY', reasons=[],
        source_identity=source_identity, profiles=PROFILES, stocks={}, strategies={})
    try:
        opened = validate_session_calendar(calendar)
        if not calendar['start'] <= str(now.date()) <= calendar['end']:
            raise ValueError('Calendar does not cover clock')
    except (ValueError, KeyError, TypeError):
        report['reasons'] = ['CALENDAR_UNAVAILABLE']
        report['stocks'] = {c:dict(reasons=['CALENDAR_UNAVAILABLE']) for c in codes}
        report['strategies'] = {k:dict(data_ready=False, model_ready=False, reasons=['CALENDAR_UNAVAILABLE']) for k in PROFILES}
        return report
    if end_date is None or end_date not in opened or start_date > end_date:
        raise ValueError('Invalid diagnostic range')
    end = pd.Timestamp(iso_date(end_date))
    if datetime.combine(end.date(), time(16), SHANGHAI) >= now:
        raise ValueError('Diagnostic end is not a completed session')
    effective = max(start_date, calendar['start'])
    if effective > end_date: raise ValueError('Calendar history not available')
    report.update(target_trade_date=end_date, effective_start=effective,
                  range_shortened=effective != start_date)
    frame = normalize(daily)
    basic = normalize(basic, 'list_date')
    factor = normalize(factors)
    for f in (frame, factor):
        if f is None or f.empty: continue
        if (not {'ts_code','trade_date'}.issubset(f) or f.trade_date.isna().any()
                or f.duplicated(['ts_code','trade_date']).any() or not set(f.ts_code).issubset(codes)
                or not f.trade_date.equals(f.trade_date.dt.normalize()) or (f.trade_date > end).any()):
            raise ValueError('Invalid source history identities/range')
    if not basic.empty and ('ts_code' not in basic or basic.ts_code.duplicated().any()):
        raise ValueError('Invalid basic stock identities')
    daily_groups = {c:g for c,g in frame.groupby('ts_code')} if 'ts_code' in frame else {}
    factor_groups = {c:g for c,g in factor.groupby('ts_code')} if factor is not None and 'ts_code' in factor else {}
    evaluations = {key:{} for key in PROFILES}
    for code in codes:
        rows = daily_groups.get(code, frame.iloc[:0])
        stock_basic = basic[basic.ts_code == code] if 'ts_code' in basic else basic.iloc[:0]
        listing = stock_basic.list_date.iloc[0] if 'list_date' in stock_basic and not stock_basic.empty else pd.NaT
        reasons = []
        if pd.isna(listing): reasons.append('UNKNOWN_LISTING')
        stock_start = max(pd.Timestamp(effective), listing) if not pd.isna(listing) else pd.Timestamp(effective)
        expected = [pd.Timestamp(d) for d in opened if stock_start <= pd.Timestamp(d) <= end]
        history = rows[rows.trade_date.isin(expected)] if 'trade_date' in rows else rows
        missing = len(set(expected)-set(history.trade_date)) if 'trade_date' in history else len(expected)
        if missing: reasons.append('MISSING_SESSIONS')
        if rows.empty: reasons.append('MISSING_STOCK')
        if not expected: reasons.append('NO_LISTED_HISTORY')
        valid_daily = _valid(history, DAILY)
        if not valid_daily: reasons.append('INVALID_DAILY')
        report['stocks'][code] = dict(effective_start=str(stock_start.date()), list_date=None if pd.isna(listing) else str(listing.date()),
             first_observed=None if rows.empty else str(rows.trade_date.min().date()),
             last_observed=None if rows.empty else str(rows.trade_date.max().date()),
             daily_missing_count=missing, observed_sessions=len(history), expected_sessions=len(expected), reasons=reasons)
        for key, profile in PROFILES.items():
            issues = list(reasons)
            if not _valid(history, profile['required']): issues.append('MISSING_REQUIRED_FIELDS')
            if key == 'transformer':
                if not factor_verified: issues.append('UNVERIFIED_FACTORS')
                f = factor_groups.get(code)
                if f is None or not set(expected).issubset(set(f.trade_date)) or not _valid(f[f.trade_date.isin(expected)], ['adj_factor']):
                    issues.append('MISSING_FACTORS')
            count = max(0, len(history)-profile['warmup']-profile['sequence']+2-profile['horizon'])
            if count < profile['mature']: issues.append('INSUFFICIENT_WARMUP_HISTORY')
            warm_index = profile['warmup']+profile['sequence']-2
            evaluation_start = str(expected[warm_index].date()) if len(expected) > warm_index else None
            evaluations[key][code] = dict(data_ready=not issues, mature_samples=count,
                reasons=sorted(set(issues)), evaluation_start=evaluation_start)
    for key in PROFILES:
        relevant = [selected_stock] if key == 'single_xgb' else codes
        per_stock = {c:evaluations[key][c] for c in relevant}
        ready = all(v['data_ready'] for v in per_stock.values())
        report['strategies'][key] = dict(data_ready=ready, model_ready=False, reason='MODEL_NOT_TRAINED_FOR_POOL',
            reasons=sorted({issue for v in per_stock.values() for issue in v['reasons']}), stocks=per_stock,
            common_evaluation_start=max(v['evaluation_start'] for v in per_stock.values()) if ready else None)
    report['status'] = 'DATA_READY' if all(v['data_ready'] for v in report['strategies'].values()) else 'NOT_READY'
    return report
