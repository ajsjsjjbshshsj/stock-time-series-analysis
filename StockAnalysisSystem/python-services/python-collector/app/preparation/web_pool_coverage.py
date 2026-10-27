"""Page-bounded existence coverage; no price audits, acquisition or readiness claims."""
from datetime import datetime,timedelta
from app.preparation.contracts import TZ,iso,identity,timestamp
from app.nightly.contracts import _reference
from app.nightly.symbol_periods import reference_rows,eligible_on
from app.preparation.gap_suspensions import cached_no_quote
from app.repositories.web_collection_repository import date_range,page
from app.repositories.web_pool_repository import summary


class CoverageUnavailable(RuntimeError): pass


class PoolCoverage:
    def __init__(self,pools,coverage,*,calendar_loader,reference_loader,evidence_store,clock=None):
        self.pools,self.coverage=pools,coverage
        self.calendar_loader,self.reference_loader=calendar_loader,reference_loader
        self.evidence_store=evidence_store
        self.clock=clock or (lambda:datetime.now(TZ))

    def inspect(self,version,start,end,*,limit=20,offset=0):
        page(limit,offset);first,last=date_range(start,end,self.clock())
        pool=self.pools.get(version);codes=pool['codes'][offset:offset+limit]
        # Request validation stays outside this boundary. Trusted local readers
        # can also raise ValueError; those are dependency failures, not bad input.
        try:
            return self._inspect(pool,codes,start,end,first,last,offset)
        except CoverageUnavailable:
            raise
        except Exception:
            raise CoverageUnavailable('TRUSTED_COVERAGE_UNAVAILABLE') from None

    def _inspect(self,pool,codes,start,end,first,last,offset):
        calendar=self.calendar_loader(start,end,self.clock())
        if not isinstance(calendar,dict) or calendar.get('source')!='tushare.trade_cal':
            raise CoverageUnavailable('CALENDAR_UNAVAILABLE')
        days={}
        for row in calendar['rows']:
            day=iso(row['cal_date']).isoformat()
            if day in days or type(row['is_open']) is not int or row['is_open'] not in (0,1):
                raise CoverageUnavailable('CALENDAR_INVALID')
            days[day]=row['is_open']
        required=[(first+timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
        if any(day not in days for day in required):raise CoverageUnavailable('CALENDAR_UNAVAILABLE')
        sessions=[day for day in required if days[day]]
        bundle=self.reference_loader()
        if bundle is None:raise CoverageUnavailable('REFERENCE_UNAVAILABLE')
        acquired=timestamp(bundle['acquired_at']);selected=set(codes)
        reference={}
        for raw in reference_rows(bundle['rows']):
            if raw['ts_code'] in selected:
                row=_reference(raw,acquired)
                if row['ts_code'] in reference:raise CoverageUnavailable('REFERENCE_DUPLICATE')
                reference[row['ts_code']]=row
        valid={code:{day for day in sessions if eligible_on(code,day,list_date=reference[code]['list_date'],
            delist_date=reference[code]['delist_date'])} for code in codes if code in reference}
        keys={name:set(self.coverage.iter_keys(name,codes,start,end)) if codes else set()
            for name in ('stock_daily','stock_daily_basic')}
        absent={(code,day) for code in valid for day in valid[code] if (code,day) not in keys['stock_daily']}
        proofs=cached_no_quote(self.evidence_store,absent)
        items=[]
        for code in codes:
            if code not in valid:
                items.append(dict(ts_code=code,status='REFERENCE_UNAVAILABLE',eligible_sessions=None,daily_rows=None,
                    daily_basic_rows=None,unresolved_no_quote=None,verified_no_quote=None,missing_basic_for_quotes=None,
                    stored_first=None,stored_last=None,gap_dates_sample=[]))
                continue
            dates=valid[code]
            daily={d for c,d in keys['stock_daily'] if c==code and d in dates}
            basic={d for c,d in keys['stock_daily_basic'] if c==code and d in dates}
            suspended={d for c,d in proofs if c==code}
            unresolved=dates-daily-suspended
            basic_missing=daily-basic
            outside=sum(c==code and d not in dates for c,d in keys['stock_daily'])
            state='NO_APPLICABLE_SESSIONS' if not dates else 'NEEDS_CHECK' if unresolved or basic_missing or outside else 'ROW_COVERED'
            items.append(dict(ts_code=code,status=state,eligible_sessions=len(dates),daily_rows=len(daily),daily_basic_rows=len(basic),
                unresolved_no_quote=len(unresolved),verified_no_quote=len(suspended),missing_basic_for_quotes=len(basic_missing),
                out_of_period_daily_rows=outside,stored_first=min(daily) if daily else None,stored_last=max(daily) if daily else None,
                gap_dates_sample=sorted(unresolved)[:5]))
        fields=('eligible_sessions','daily_rows','daily_basic_rows','unresolved_no_quote','verified_no_quote','missing_basic_for_quotes')
        totals={key:sum(row[key] for row in items if row[key] is not None) for key in fields}
        return dict(pool=summary(pool),start=start,end=end,items=items,total=pool['stock_count'],offset=offset,
            summary_scope='current_page',summary=totals,unavailable_stocks=sum(row['eligible_sessions'] is None for row in items),
            calendar_sha256=identity(calendar),reference_sha256=bundle['manifest_sha256'],reference_acquired_at=bundle['acquired_at'],
            reference_older_than_range=acquired.date()<last,price_values_validated=False,model_ready=False,
            note='Unexplained no-quote dates need verification; fixed membership is not historical point-in-time constituents.')
