"""Bounded missing-key requests; never refetch complete shared-stock history."""
from collections import defaultdict
from datetime import date, timedelta

from app.market_data.repair_contract import request
from .contracts import iso


def plan_missing(inventory: dict) -> list[dict]:
    if inventory.get('missing_reference') or inventory['window'].get('empty'):
        return []
    grouped = {}
    for method,key in (('daily','missing_daily'),('suspend_d','missing_suspensions'),('daily_basic','missing_basic')):
        dates = defaultdict(set)
        for code,day in inventory[key]: dates[day].add(code)
        grouped[method] = dates
    result = []
    days = sorted(set().union(*(set(v) for v in grouped.values())))
    for day in days:
        for method in ('daily','suspend_d','daily_basic'):
            codes = sorted(grouped[method].get(day,()))
            if codes: result.append(request(method,codes,day,day))
    missing = defaultdict(set)
    for code,day in inventory['missing_factors']: missing[code].add(iso(day))
    sessions=sorted({r['trade_date'] for r in inventory['attribution']['status']})
    positions={day:i for i,day in enumerate(sessions)}
    for code in sorted(missing):
        dates = sorted(missing[code])
        start = stop = dates[0]
        for current in dates[1:]:
            if current.year==stop.year and positions[current.isoformat()]==positions[stop.isoformat()]+1:
                stop=current
            else:
                result.append(request('adj_factor',[code],start.isoformat(),stop.isoformat()))
                start=stop=current
        result.append(request('adj_factor',[code],start.isoformat(),stop.isoformat()))
    return result
