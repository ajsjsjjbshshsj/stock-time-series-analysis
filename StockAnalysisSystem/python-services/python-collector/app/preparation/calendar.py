"""Resolve completed sessions strictly from a complete real trading calendar."""
from datetime import datetime, timedelta, time

from .contracts import TZ, iso


def resolve_window(requested_start: str, requested_end: str, calendar: dict, now) -> dict:
    start, end = iso(requested_start), iso(requested_end)
    if start > end or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('INVALID_WINDOW_OR_CLOCK')
    local = now.astimezone(TZ)
    candidate = local.date() if local.time() > time(16) else local.date() - timedelta(days=1)
    if calendar.get('source') != 'tushare.trade_cal':
        raise ValueError('REAL_CALENDAR_REQUIRED')
    keyed = {}
    for row in calendar['rows']:
        day = iso(row['cal_date'])
        if day in keyed or type(row['is_open']) is not int or row['is_open'] not in (0, 1):
            raise ValueError('INVALID_CALENDAR')
        keyed[day] = row['is_open']
    lower = min(start, candidate)
    if not keyed or lower < min(keyed):
        raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    upper = min(end, candidate)
    required_start = min(keyed) if start > candidate else lower
    required = [required_start + timedelta(days=i) for i in range((upper-required_start).days+1)]
    if any(day not in keyed for day in required):
        raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    sessions = [day for day, opened in keyed.items() if opened and day <= upper]
    actual_end = max(sessions) if sessions else upper
    empty = not sessions or actual_end < start
    reason = ('EMPTY_EFFECTIVE_RANGE' if empty else
              'CLAMPED_TO_COMPLETED_SESSION' if actual_end < end else None)
    return dict(requested_start=requested_start, requested_end=requested_end,
                start=start.isoformat(), end=actual_end.isoformat(), cutoff_reason=reason, empty=empty)
