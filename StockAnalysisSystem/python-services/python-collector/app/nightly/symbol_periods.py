"""Independent code validity boundaries; never map or copy another code's data.

The 2025-02-17 boundaries are documented by issuer announcement 2025-028:
https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-15/cedb693a-f5ee-4463-9682-ea33d406b569.PDF
These boundaries are distinct from the actual stock listing date.
"""
from datetime import date

from app.nightly.contracts import START, is_a_share


_PERIODS = {
    '300114.SZ': (None, '2025-02-17'),
    '302132.SZ': ('2025-02-17', None),
}

# Archival profile, independently verified from the exchange's 2010 listing
# register. L means listed within this historical code period, NOT current L/D/P
# status. Unknown historical area/industry remain null; prices are never derived.
# https://www.szse.cn/aboutus/sse/events/t20110119_497844.html
_ARCHIVED = ({
    'ts_code':'300114.SZ','symbol':'300114','name':'中航电测',
    'area':None,'industry':None,'market':'创业板','exchange':'SZSE',
    'curr_type':'CNY','list_status':'L','list_date':'20100827','delist_date':None,
},)


def reference_rows(rows):
    """Supplement only absent archival profiles; never derive from other codes."""
    result=[dict(row) for row in rows]
    seen={row['ts_code'] for row in result}
    result.extend(dict(row) for row in _ARCHIVED if row['ts_code'] not in seen)
    return result


def period_for(code):
    """Return a fresh, independent inclusive-start/exclusive-end description."""
    start, end = _PERIODS.get(code, (None, None))
    return dict(valid_from=start, valid_until=end)


def _day(value):
    if type(value) is not str:
        raise TypeError('SYMBOL_PERIOD_DATE_INVALID')
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('SYMBOL_PERIOD_DATE_INVALID')
    return parsed


def eligible_on(code, day, *, list_date, delist_date=None):
    """Intersect retained history, actual listing lifecycle and code validity."""
    trading_day = _day(day)
    listing_day = _day(list_date)
    delisting_day = _day(delist_date) if delist_date is not None else None
    if delisting_day is not None and delisting_day < listing_day:
        raise ValueError('SYMBOL_PERIOD_LIFECYCLE_INVALID')
    if not is_a_share(code):
        return False
    period = period_for(code)
    return (
        trading_day >= _day(START)
        and trading_day >= listing_day
        and (delisting_day is None or trading_day < delisting_day)
        and (period['valid_from'] is None or trading_day >= _day(period['valid_from']))
        and (period['valid_until'] is None or trading_day < _day(period['valid_until']))
    )
