"""Pure, compact coverage and missing-only planning contracts."""
from datetime import timedelta
from bisect import bisect_left
import re

from app.preparation.contracts import identity, iso, timestamp
from app.nightly.contracts import SCOPE, START, _reference, excluded_legacy_reference, is_a_share
from app.nightly.symbol_periods import eligible_on, period_for
from app.market_data.repair_contract import DAILY, BASIC

STRATEGY = 'market-gap-v1'


def _member_bits(row, sessions):
    period = period_for(row['ts_code'])
    first = max(START, row['list_date'], period['valid_from'] or START)
    last = min(value for value in (row['delist_date'], period['valid_until'], '9999-12-31') if value)
    lower, upper = bisect_left(sessions, first), bisect_left(sessions, last)
    return ((1 << max(0, upper-lower))-1) << lower


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
                and _member_bits(row, sessions)):
            normalized.append(row)
    normalized.sort(key=lambda row: row['ts_code'])
    roster = {row['ts_code']: row for row in normalized}
    valid = {row['ts_code']: _member_bits(row, sessions) for row in normalized}
    index = {day: i for i, day in enumerate(sessions)}
    bitmaps = {}
    counts = {}
    for table, stream in (('stock_daily', daily_keys), ('stock_daily_basic', basic_keys)):
        bits = dict.fromkeys(roster, 0)
        for code, day in stream:
            day = iso(day).isoformat()
            if code not in roster or day not in index:
                continue
            flag = 1 << index[day]
            if not valid[code] & flag:
                continue
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


def validate_coverage(snapshot):
    if (snapshot.get('strategy') != STRATEGY or snapshot.get('schema_version') != 1
            or snapshot.get('scope') != SCOPE
            or snapshot.get('coverage_sha256') != identity({
                key: value for key, value in snapshot.items() if key != 'coverage_sha256'})):
        raise ValueError('GAP_COVERAGE_HASH_INVALID')
    return snapshot


def eligible_bits(snapshot, *, codes=None):
    selected = set(codes) if codes is not None else None
    return {row['ts_code']: _member_bits(row, snapshot['sessions'])
        for row in snapshot['members'] if selected is None or row['ts_code'] in selected}


def make_gap_request(method, mode, codes, expected_keys, *, snapshot):
    validate_coverage(snapshot)
    return _make_request(method, mode, codes, expected_keys, snapshot,
        eligible_bits(snapshot, codes=codes), {day: i for i, day in enumerate(snapshot['sessions'])})


def _make_request(method, mode, codes, expected_keys, snapshot, valid, index):
    if method not in ('daily', 'daily_basic') or mode not in ('stock_range', 'full_date'):
        raise ValueError('GAP_REQUEST_METHOD_INVALID')
    codes, keys = list(codes), [list(key) for key in expected_keys]
    if (not codes or codes != sorted(set(codes)) or not keys or keys != sorted(keys)
            or len({tuple(key) for key in keys}) != len(keys)
            or sorted({key[0] for key in keys}) != codes):
        raise ValueError('GAP_REQUEST_KEYS_INVALID')
    present = snapshot['daily_bits' if method == 'daily' else 'basic_bits']
    for code, day in keys:
        if (code not in valid or day not in index or not valid[code] & (1 << index[day])
                or int(present[code], 16) & (1 << index[day])
                or method=='daily' and int(snapshot.get('no_quote_bits',{}).get(code,'0'),16) & (1 << index[day])):
            raise ValueError('GAP_REQUEST_OUTSIDE_MISSING_KEYS')
    start, end = min(key[1] for key in keys), max(key[1] for key in keys)
    fields = list(DAILY if method == 'daily' else BASIC)
    if mode == 'stock_range':
        expected_days = snapshot['sessions'][index[start]:index[end]+1]
        if (len(codes) != 1 or start[:4] != end[:4]
                or [key[1] for key in keys] != expected_days):
            raise ValueError('GAP_RANGE_NOT_CONTIGUOUS')
        params = dict(ts_code=codes[0], start_date=start.replace('-', ''),
                      end_date=end.replace('-', ''), fields=','.join(fields))
    else:
        if (start != end or any(int(bits, 16) & (1 << index[start]) for bits in present.values())
                or len(keys) >= 6000):
            raise ValueError('GAP_FULL_DATE_NOT_ENTIRELY_MISSING')
        params = dict(trade_date=start.replace('-', ''), fields=','.join(fields))
    body = dict(strategy=STRATEGY, method=method, mode=mode, expected_codes=codes,
        expected_keys=keys, start=start, end=end, fields=fields, params=params, capacity=6000,
        coverage_sha256=snapshot['coverage_sha256'], reference_sha256=snapshot['reference_sha256'],
        calendar_sha256=snapshot['calendar_sha256'])
    return dict(body, id=identity(body))


def _plan(snapshot, method, missing, valid):
    missing = {code:bits for code,bits in missing.items() if bits}
    days = snapshot['sessions']
    index = {day: i for i, day in enumerate(days)}
    present = snapshot['daily_bits' if method == 'daily' else 'basic_bits']
    result = []
    # Full-date requests are exceptional: prove NO eligible stored keys exist.
    relevant = 0
    for bits in missing.values():relevant |= bits
    for i, day in enumerate(days):
        flag = 1 << i
        if not relevant & flag:continue
        targets = [code for code in sorted(missing) if missing[code] & flag]
        if (targets and len(targets) < 6000
                and not any(int(bits, 16) & flag for bits in present.values())):
            result.append(_make_request(method, 'full_date', targets,
                [(code, day) for code in targets], snapshot, valid, index))
            for code in targets:
                missing[code] &= ~flag
    for code in sorted(missing):
        run = []
        def flush():
            if run:
                result.append(_make_request(method, 'stock_range', [code],
                    [(code, day) for day in run], snapshot, valid, index))
                run.clear()
        for i, day in enumerate(days):
            if missing[code] & (1 << i):
                if run and run[-1][:4] != day[:4]:
                    flush()
                run.append(day)
            else:
                flush()
        flush()
    return sorted(result, key=lambda item: (item['expected_codes'], item['start'], item['method']))


def plan_daily(snapshot):
    validate_coverage(snapshot)
    valid = eligible_bits(snapshot)
    missing = {code: bits & ~int(snapshot['daily_bits'][code], 16)
        & ~int(snapshot.get('no_quote_bits',{}).get(code,'0'),16) for code, bits in valid.items()}
    return _plan(snapshot, 'daily', missing, valid)


def plan_basic(snapshot, new_daily_keys, *, include_existing=True):
    validate_coverage(snapshot)
    new_daily_keys = list(new_daily_keys)
    valid = eligible_bits(snapshot, codes=None if include_existing else {key[0] for key in new_daily_keys})
    actual = {code: int(snapshot['daily_bits'][code], 16) if include_existing else 0 for code in valid}
    index = {day: i for i, day in enumerate(snapshot['sessions'])}
    for code, day in new_daily_keys:
        if code not in valid or day not in index or not valid[code] & (1 << index[day]):
            raise ValueError('NEW_QUOTE_OUTSIDE_COVERAGE')
        actual[code] |= 1 << index[day]
    missing = {code: actual[code] & ~int(snapshot['basic_bits'][code], 16) for code in valid}
    return _plan(snapshot, 'daily_basic', missing, valid)
