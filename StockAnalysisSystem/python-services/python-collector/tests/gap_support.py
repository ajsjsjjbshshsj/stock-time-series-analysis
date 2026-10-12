"""Complete handwritten reference/calendar inputs, never a live data source."""
from datetime import date, timedelta
from nightly_support import reference


def coverage_fixture(days, *, codes=('000001.SZ',), daily_keys=(), basic_keys=(), changes=None):
    from app.preparation.gap_plan import make_coverage_snapshot
    first, last = date.fromisoformat(min(days)), date.fromisoformat(max(days))
    sessions = set(days)
    calendar = dict(source='tushare.trade_cal', rows=[
        dict(cal_date=(first + timedelta(days=i)).isoformat(),
             is_open=int((first + timedelta(days=i)).isoformat() in sessions))
        for i in range((last-first).days+1)])
    members = [reference(code, exchange='SSE' if code.endswith('.SH') else 'SZSE',
                         **(changes or {}).get(code, {})) for code in codes]
    return make_coverage_snapshot(members, calendar, first.isoformat(), last.isoformat(),
        iter(daily_keys), iter(basic_keys), reference_sha256='a'*64,
        acquired_at='2026-10-12T10:00:00+08:00')
