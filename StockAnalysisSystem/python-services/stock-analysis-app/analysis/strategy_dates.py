"""Pure completed-session selection. This module never collects or fills prices."""
from datetime import datetime, time, timedelta, timezone
import re

import numpy as np
import pandas as pd

SHANGHAI = timezone(timedelta(hours=8))


def validate_session_calendar(calendar):
    if (not isinstance(calendar, dict) or calendar.get('source') != 'tushare.trade_cal'
            or calendar.get('exchange') not in ('SSE', 'SZSE')):
        raise ValueError('Trusted exchange calendar required')
    try:
        start, end = (datetime.strptime(calendar[k], '%Y-%m-%d').date() for k in ('start', 'end'))
        if not 0 <= (end-start).days <= 10000:
            raise ValueError('Invalid calendar span')
        expected = [str(d.date()) for d in pd.date_range(start, end)]
        rows = calendar['rows']
        if (not isinstance(rows, list) or len(rows) != len(expected)
                or any(not isinstance(r, dict) or set(r) != {'cal_date', 'is_open'}
                       or r['cal_date'] != d or type(r['is_open']) is not int
                       or r['is_open'] not in (0, 1) for d, r in zip(expected, rows))):
            raise ValueError('Calendar coverage/flags invalid')
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('Calendar schema invalid') from exc
    opened = [r['cal_date'] for r in rows if r['is_open']]
    if not opened:
        raise ValueError('Calendar has no trading sessions')
    return opened


def market_dates(frame, required):
    """Preserve incomplete numeric rows for explicit fallback, reject bad identities."""
    if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
            or not {'ts_code', 'trade_date', *required}.issubset(frame)):
        raise ValueError('Incomplete market schema')
    result = frame.copy()
    parsed = pd.to_datetime(result.trade_date, errors='raise')
    if (parsed.isna().any() or parsed.dt.tz is not None
            or not parsed.equals(parsed.dt.normalize())):
        raise ValueError('Invalid market dates')
    result['trade_date'] = parsed.dt.strftime('%Y-%m-%d')
    if (result.duplicated(['ts_code', 'trade_date']).any()
            or not result.ts_code.map(lambda c: isinstance(c, str)
                                      and bool(re.fullmatch(r'\d{6}\.(SZ|SH|BJ)', c))).all()):
        raise ValueError('Invalid market identities')
    return result


def _complete(day, codes, columns):
    if set(day.ts_code) != set(codes) or len(day) != len(codes):
        return False
    for column in columns:
        if day[column].map(lambda v: isinstance(v, (bool, np.bool_))).any():
            return False
        values = pd.to_numeric(day[column], errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(values).all():
            return False
        if column in ('open', 'high', 'low', 'close', 'adj_factor'):
            if (values <= 0).any(): return False
        elif column in ('vol', 'amount') and (values < 0).any():
            return False
    if {'open', 'high', 'low', 'close'}.issubset(columns):
        if ((day.high < day[['open', 'close', 'low']].max(axis=1))
                | (day.low > day[['open', 'close', 'high']].min(axis=1))).any():
            return False
    return True


def select_data_cutoff(calendar, request_time, daily, codes, *, factors=None,
                       required_columns=('open', 'high', 'low', 'close', 'vol', 'amount')):
    opened = validate_session_calendar(calendar)
    if not isinstance(request_time, datetime) or request_time.utcoffset() is None:
        raise ValueError('Timezone-aware request time required')
    now = request_time.astimezone(SHANGHAI)
    if not calendar['start'] <= str(now.date()) <= calendar['end']:
        raise ValueError('Calendar does not cover request date')
    if (not isinstance(codes, (list, tuple)) or not codes or len(set(codes)) != len(codes)
            or any(not isinstance(c, str) or not re.fullmatch(r'\d{6}\.(SZ|SH|BJ)', c) for c in codes)):
        raise ValueError('Fixed stock pool required')
    eligible = [d for d in opened if datetime.combine(datetime.fromisoformat(d).date(),
                                                     time(16), SHANGHAI) < now]
    if not eligible:
        raise ValueError('No completed session in calendar')
    target = eligible[-1]
    frame = market_dates(daily, required_columns)
    factor_frame = market_dates(factors, ('adj_factor',)) if factors is not None else None
    reason = None
    for day in reversed(eligible):
        prices = frame[(frame.trade_date == day) & frame.ts_code.isin(codes)]
        if not _complete(prices, codes, required_columns):
            if day == target: reason = 'INCOMPLETE_DAILY'
            continue
        if factor_frame is not None and not _complete(
                factor_frame[(factor_frame.trade_date == day) & factor_frame.ts_code.isin(codes)],
                codes, ('adj_factor',)):
            if day == target: reason = 'INCOMPLETE_FACTORS'
            continue
        return dict(request_time=now.isoformat(), target_trade_date=target, data_cutoff=day,
                    fallback_reason=reason if day != target else None)
    raise ValueError('No complete eligible market session')
