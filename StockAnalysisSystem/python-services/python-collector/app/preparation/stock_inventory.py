"""Pure stock-day inventory and attribution; no database or SDK calls."""
from collections import Counter
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from app.market_data.repair_contract import DAILY, BASIC, canonical
from app.market_data.repair_snapshot import classify_sources
from .contracts import iso


def _number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError('INVALID_NUMERIC')
    number = Decimal(str(value))
    if not number.is_finite(): raise ValueError('INVALID_NUMERIC')
    return number


def _existing_invalid(table, row):
    try:
        if table == 'stock_daily':
            numbers = {k:_number(row.get(k)) for k in DAILY[2:]}
            if any(numbers[k]<=0 for k in ('open','high','low','close','pre_close')):
                return True
            if numbers['vol']<0 or numbers['amount']<0:
                return True
            return not numbers['low'] <= min(numbers['open'],numbers['close']) <= max(numbers['open'],numbers['close']) <= numbers['high']
        for field in BASIC[2:]:
            value = row.get(field)
            if field not in row: return True
            if value is not None:
                number = _number(value)
                if field in ('turnover_rate','total_mv') and number<0: return True
        return False
    except (ValueError,InvalidOperation,TypeError):
        return True


def build_stock_inventory(pool: dict, window: dict, tables: dict, evidence: dict) -> dict:
    start, end = iso(window['start']), iso(window['end'])
    tables = canonical(tables)
    missing_reference = sorted(set(pool['codes'])-{r['ts_code'] for r in tables['stock_basic'] if r.get('list_date')})
    base = dict(pool=pool,window=window,baseline=tables,stock_count=pool['stock_count'],
                missing_reference=missing_reference,missing_daily=[],missing_basic=[],
                missing_factors=[],missing_suspensions=[],coverage={},issues={},data_ready=False)
    if window.get('empty'):
        base.update(issues={'EMPTY_EFFECTIVE_RANGE':1})
        return base
    if missing_reference:
        base.update(issues={'MISSING_REFERENCE':len(missing_reference)})
        return base
    if (tables['start'],tables['end']) != (window['start'],window['end']):
        raise ValueError('INVENTORY_WINDOW_MISMATCH')
    calendar = evidence.get('calendar',{})
    if calendar.get('source') != 'tushare.trade_cal':
        raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    keyed = {}
    for row in calendar['rows']:
        d = iso(row['cal_date'])
        if d in keyed or type(row['is_open']) is not int or row['is_open'] not in (0,1):
            raise ValueError('INVALID_CALENDAR')
        keyed[d] = row['is_open']
    if any(start+timedelta(days=i) not in keyed for i in range((end-start).days+1)):
        raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    dates = {d.isoformat() for d,opened in keyed.items() if opened and start<=d<=end}
    listed = {r['ts_code']:iso(r['list_date']).isoformat() for r in tables['stock_basic']}
    if len(listed) != len(tables['stock_basic']) or set(listed) != set(pool['codes']):
        raise ValueError('IPO_IDENTITY_INVALID')
    invalid_keys = set()
    for table in ('stock_daily','stock_daily_basic'):
        keys = set()
        for row in tables[table]:
            key = row['ts_code'],row['trade_date']
            if key in keys or key[0] not in listed or key[1] not in dates or key[1]<listed[key[0]]:
                raise ValueError('DATABASE_ROW_OUTSIDE_INVENTORY')
            keys.add(key)
            if _existing_invalid(table,row): invalid_keys.add((table,*key))
    sources = {method:[] for method in ('daily','daily_basic','adj_factor','suspend_d')}
    for method in sources:
        for raw in evidence.get(method,[]):
            row = dict(raw)
            day = str(row['trade_date'])
            row['trade_date'] = f'{day[:4]}-{day[4:6]}-{day[6:]}' if len(day)==8 else day
            if row['ts_code'] in listed and row['trade_date'] in dates and row['trade_date']>=listed[row['ts_code']]:
                sources[method].append(row)
    result = classify_sources(dict(pool=pool,calendar=calendar,tables=tables),sources)
    issues = dict(result['report']['source_issue_counts'],INVALID_EXISTING_DATA=len(invalid_keys))
    queried = {tuple(k) for k in evidence.get('queried_suspensions',[])}
    statuses = result['status']
    base.update(issues=issues,coverage=dict(Counter(r['daily_status'] for r in statuses)),attribution=result)
    for row in statuses:
        key = [row['ts_code'],row['trade_date']]
        if row['daily_status']=='UNRESOLVED_MISSING':
            base['missing_daily'].append(key)
            if tuple(key) not in queried: base['missing_suspensions'].append(key)
        if row['basic_status']=='MISSING_BASIC_ROW' and row['daily_status'] not in ('PRE_LISTING','FULL_DAY_SUSPENSION','UNRESOLVED_MISSING','SOURCE_CONFLICT'):
            base['missing_basic'].append(key)
        if row['factor_status']=='MISSING_FACTOR': base['missing_factors'].append(key)
    base['data_ready'] = not any(issues.values())
    return base
