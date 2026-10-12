"""Pure, compact coverage and missing-only planning contracts."""
from datetime import timedelta
import re

from app.preparation.contracts import identity, iso, timestamp
from app.nightly.contracts import SCOPE, START, _reference, excluded_legacy_reference, is_a_share
from app.nightly.symbol_periods import eligible_on

STRATEGY = 'market-gap-v1'


def make_coverage_snapshot(members, calendar, start, end, daily_keys, basic_keys,
                           *, reference_sha256, acquired_at):
    first, last, acquired = iso(start), iso(end), timestamp(acquired_at)
    if (first < iso(START) or first > last or last > acquired.date()
            or type(reference_sha256) is not str or re.fullmatch('[0-9a-f]{64}', reference_sha256) is None):
        raise ValueError('GAP_SCOPE_OR_REFERENCE_INVALID')
    if not isinstance(calendar, dict) or calendar.get('source') != 'tushare.trade_cal':
        raise ValueError('CALENDAR_SOURCE_INVALID')
    days = {}
    for row in calendar['rows']:
        day = iso(row['cal_date']).isoformat()
        if day in days or type(row['is_open']) is not int or row['is_open'] not in (0, 1):
            raise ValueError('CALENDAR_RESPONSE_INVALID')
        days[day] = row['is_open']
    required = [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    if any(day not in days for day in required):
        raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    sessions = [day for day in required if days[day]]
    normalized, seen = [], set()
    for raw in members:
        if excluded_legacy_reference(raw, acquired) is not None:
            continue
        row = _reference(raw, acquired)
        code = row['ts_code']
        if code in seen:
            raise ValueError('REFERENCE_DUPLICATE')
        seen.add(code)
        if (is_a_share(code, row['exchange']) and row['curr_type'] == 'CNY'
                and any(eligible_on(code, day, list_date=row['list_date'],
                                    delist_date=row['delist_date']) for day in sessions)):
            normalized.append(row)
    normalized.sort(key=lambda row: row['ts_code'])
    roster = {row['ts_code']: row for row in normalized}
    index = {day: i for i, day in enumerate(sessions)}
    bitmaps = {}
    counts = {}
    for table, stream in (('stock_daily', daily_keys), ('stock_daily_basic', basic_keys)):
        bits = dict.fromkeys(roster, 0)
        for code, day in stream:
            day = iso(day).isoformat()
            if code not in roster or day not in index:
                continue
            member = roster[code]
            if not eligible_on(code, day, list_date=member['list_date'], delist_date=member['delist_date']):
                continue
            flag = 1 << index[day]
            if bits[code] & flag:
                raise ValueError('GAP_KEY_DUPLICATE')
            bits[code] |= flag
        bitmaps[table] = {code: format(value, 'x') for code, value in bits.items()}
        counts[table] = sum(value.bit_count() for value in bits.values())
    result = dict(schema_version=1, strategy=STRATEGY, scope=SCOPE, start=start, end=end,
        sessions=sessions, members=normalized, daily_bits=bitmaps['stock_daily'],
        basic_bits=bitmaps['stock_daily_basic'], reference_sha256=reference_sha256,
        acquired_at=acquired.isoformat(), calendar_sha256=identity([(day, days[day]) for day in required]),
        existing_counts=counts)
    result['coverage_sha256'] = identity(result)
    return result
