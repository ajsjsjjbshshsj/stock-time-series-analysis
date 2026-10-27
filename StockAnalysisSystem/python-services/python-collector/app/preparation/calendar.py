"""Resolve completed sessions strictly from a complete real trading calendar."""
from datetime import datetime, timedelta, time
from pathlib import Path

from app.market_data.forward_snapshot import read,write,digest
from .contracts import identity

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


class CalendarStore:
    """Sealed calendar cache; only missing ranges call the shared checked source.

    Vendor field contract: https://tushare.pro/document/2?doc_id=26
    """
    def __init__(self,data_root,requests_factory,client_factory):
        self.root=Path(data_root).resolve(); self.requests_factory=requests_factory
        self.client_factory=client_factory

    @staticmethod
    def _normalize(calendar):
        if calendar.get('source')!='tushare.trade_cal': raise ValueError('REAL_CALENDAR_REQUIRED')
        rows=[];seen=set()
        for row in calendar['rows']:
            day=iso(row['cal_date']).isoformat()
            if day in seen or type(row['is_open']) is not int or row['is_open'] not in (0,1):
                raise ValueError('INVALID_CALENDAR')
            seen.add(day); rows.append(dict(cal_date=day,is_open=row['is_open']))
        if not rows: raise ValueError('EMPTY_CALENDAR')
        return dict(source='tushare.trade_cal',rows=sorted(rows,key=lambda r:r['cal_date']))

    def register(self,calendar):
        body=self._normalize(calendar)
        folder=self.root/'calendar'; folder.mkdir(parents=True,exist_ok=True)
        path=folder/(identity(body)+'.json'); proof=path.with_suffix('.proof.json')
        if path.exists():
            if read(path)!=body or not proof.exists() or read(proof)!=dict(sha256=digest(path)):
                raise ValueError('CHANGED_CALENDAR_CACHE')
        else:
            write(path,body,exclusive=True); write(proof,dict(sha256=digest(path)),exclusive=True)
        return body

    def cached(self):
        """Read verified local rows only; never initialize or call a provider."""
        keyed={}
        for path in (self.root/'calendar').glob('*.json'):
            if path.name.endswith('.proof.json'): continue
            resolved=path.resolve()
            if not resolved.is_relative_to(self.root) or read(path.with_suffix('.proof.json'))!=dict(sha256=digest(path)):
                raise ValueError('CALENDAR_HASH_INVALID')
            body=self._normalize(read(path))
            for row in body['rows']:
                day=row['cal_date']
                if day in keyed and keyed[day]!=row['is_open']: raise ValueError('CALENDAR_SOURCE_CONFLICT')
                keyed[day]=row['is_open']
        return dict(source='tushare.trade_cal',rows=[dict(cal_date=day,is_open=keyed[day]) for day in sorted(keyed)])

    def get(self,start,end,now,*,acquire=False):
        start,end=iso(start),iso(end)
        local=now.astimezone(TZ)
        candidate=local.date() if local.time()>time(16) else local.date()-timedelta(days=1)
        upper=min(end,candidate)
        lower=start if start<=upper else upper-timedelta(days=31)
        keyed={row['cal_date']:row['is_open'] for row in self.cached()['rows']}
        required=[lower+timedelta(days=i) for i in range((upper-lower).days+1)]
        missing=[day for day in required if day.isoformat() not in keyed]
        if missing and not acquire: return None
        if missing:
            from .acquisition import source_failure
            pacing=self.requests_factory(self.root/'calendar/acquisition')
            client=self.client_factory()
            # Group consecutive missing calendar days, split at year boundaries.
            first=last=missing[0]; segments=[]
            for day in missing[1:]:
                if day==last+timedelta(days=1) and day.year==last.year: last=day
                else: segments.append((first,last)); first=last=day
            segments.append((first,last))
            for first,last in segments:
                params=dict(exchange='SSE',start_date=first.strftime('%Y%m%d'),end_date=last.strftime('%Y%m%d'),fields='exchange,cal_date,is_open')
                try: frame=pacing.call(client.trade_calendar,params)
                except RuntimeError: raise source_failure(pacing.state) from None
                if frame is None or frame.columns.duplicated().any() or set(frame.columns)!={'exchange','cal_date','is_open'}:
                    raise ValueError('CALENDAR_RESPONSE_SCHEMA')
                rows=[]
                for row in frame.to_dict('records'):
                    raw=row['cal_date']
                    if row['exchange']!='SSE' or type(raw) is not str or len(raw)!=8:
                        raise ValueError('CALENDAR_SOURCE_IDENTITY')
                    day=datetime.strptime(raw,'%Y%m%d').date()
                    opened=row['is_open']
                    if opened in ('0','1'): opened=int(opened)
                    if not first<=day<=last or type(opened) is not int or opened not in (0,1):
                        raise ValueError('CALENDAR_SOURCE_IDENTITY')
                    rows.append(dict(cal_date=day.isoformat(),is_open=opened))
                body=self.register(dict(source='tushare.trade_cal',rows=rows))
                for row in body['rows']: keyed[row['cal_date']]=row['is_open']
            if any(day.isoformat() not in keyed for day in required): raise ValueError('CALENDAR_COVERAGE_REQUIRED')
        return dict(source='tushare.trade_cal',rows=[dict(cal_date=day,is_open=keyed[day]) for day in sorted(keyed)])
