"""Explicit per-date eligibility; source history and fixed market horizon stay separate."""
from collections import Counter,defaultdict
from datetime import date,timedelta
import re
import numpy as np
import pandas as pd
from data_processor.feature_store_contract import FEATURE_COLUMNS,feature_records,date_only,validate_stock_code
from analysis.feature_store_inputs import normalize_prices


class MarketInputError(ValueError):
    def __init__(self,code,day,reason):
        self.diagnostic=dict(ts_code=validate_stock_code(code),date=str(date_only(str(day))),reason=reason)
        super().__init__('Market input failed verification')


def open_days(calendar):
    if calendar.get('source')!='tushare.trade_cal' or calendar.get('exchange')!='SSE':raise ValueError('Trusted SSE calendar required')
    start,end=date_only(calendar['start']),date_only(calendar['end'])
    if not 0<=(end-start).days<=10000:raise ValueError('Calendar span invalid')
    rows=calendar.get('rows',[])
    if len(rows)!=(end-start).days+1:raise ValueError('Complete calendar rows required')
    result=[]
    for i,row in enumerate(rows):
        day=start+timedelta(days=i)
        if set(row)!={'cal_date','is_open'} or row['cal_date']!=str(day) or type(row['is_open']) is not int or row['is_open'] not in (0,1):
            raise ValueError('Invalid calendar row')
        if row['is_open']:result.append(day)
    return result


def _ranges(days,positions):
    result=[]
    for day in days:
        if result and positions[day]==positions[date.fromisoformat(result[-1][1])]+1:result[-1][1]=str(day)
        else:result.append([str(day),str(day)])
    return result


def qualify_stock(snapshot,calendar,absence_proof,request):
    from data_processor.feature_engineer import FeatureEngineer
    from config.settings import TECHNICAL_INDICATORS
    from data_processor.feature_store_source import SOURCE_COLUMNS
    names=list(FEATURE_COLUMNS)
    if snapshot.metadata.get('feature_names')!=names:raise ValueError('Feature order mismatch')
    evidence=snapshot.metadata['source_evidence']
    if len(evidence)!=1:raise ValueError('One stock snapshot required')
    code=validate_stock_code(next(iter(evidence)))
    start,end=date_only(request['start_date']),date_only(request['end_date'])
    opened=open_days(calendar);positions={day:i for i,day in enumerate(opened)}
    if start>end or calendar['start']>str(snapshot.metadata['source_start']) or calendar['end']<str(end):
        raise ValueError('Calendar does not cover dependency history')
    requested=[day for day in opened if start<=day<=end]
    if not requested:raise ValueError('No requested trading dates')
    history=normalize_prices(snapshot.source_history)
    if set(history.ts_code)!={code}:raise ValueError('Mixed stock history')
    history=history[history.trade_date<=end].reset_index(drop=True)
    raw=normalize_prices(snapshot.raw_prices)
    expected_raw=history[history.trade_date.between(start,end)].reset_index(drop=True)
    if not raw.equals(expected_raw):raise ValueError('Request prices differ from dependency history')
    frame=snapshot.frame.copy()
    if set(frame)!={'ts_code','trade_date',*names} or frame.columns.duplicated().any():raise ValueError('Invalid feature columns')
    frame['trade_date']=[date_only(v) for v in frame.trade_date]
    if (frame.duplicated(['ts_code','trade_date']).any() or set(zip(frame.ts_code,frame.trade_date))!=set(zip(raw.ts_code,raw.trade_date))):
        raise ValueError('Feature and price keys differ')
    listed=absence_proof.get('list_date')
    if not isinstance(listed,str) or not listed:raise MarketInputError(code,start,'UNKNOWN_LISTING_BOUNDARY')
    if re.fullmatch('[0-9]{8}',listed):listed=f'{listed[:4]}-{listed[4:6]}-{listed[6:]}'
    listed=date_only(listed);suspended={date_only(v) for v in absence_proof['full_days']}
    quotes={row.trade_date:row.close for row in history.itertuples()}
    for day in quotes:
        if day<listed:raise MarketInputError(code,day,'QUOTE_BEFORE_LISTING')
    if set(quotes)-set(opened):raise ValueError('Quote outside trading calendar')
    source_start=date_only(str(snapshot.metadata['source_start']))
    for day in opened:
        if max(source_start,listed)<=day<=end and day not in quotes and day not in suspended:
            raise MarketInputError(code,day,'UNEXPLAINED_QUOTE_GAP')
    expected=None;max_warmup=max(120,max(TECHNICAL_INDICATORS['ma_periods'])-1,
        TECHNICAL_INDICATORS['rsi_period'],TECHNICAL_INDICATORS['macd_slow']+TECHNICAL_INDICATORS['macd_signal']-2)
    if not history.empty:
        calculation=history.copy()
        for name in SOURCE_COLUMNS[2:]:calculation[name]=pd.to_numeric(calculation[name]).astype(float)
        featured=FeatureEngineer().calculate_all_features(calculation)
        expected=pd.DataFrame(feature_records(featured,snapshot.metadata['build_id'])).set_index('trade_date')
    actual=frame.set_index('trade_date');hist_positions={day:i for i,day in enumerate(history.trade_date)}
    reasons=Counter();excluded=defaultdict(list);samples=[]
    for day in requested:
        reason=None
        if day<listed:reason='PRE_LISTING'
        elif day not in quotes:reason='SUSPENDED'
        else:
            values=actual.loc[day,names].to_numpy(dtype=float)
            recomputed=expected.loc[day,names].to_numpy(dtype=float)
            # Stored values remain unchanged. Tolerance only covers float64
            # compute/storage round trips, never old float32 cache conversion.
            if np.isinf(values).any() or not np.allclose(values,recomputed,rtol=1e-10,atol=1e-12,equal_nan=True):
                raise MarketInputError(code,day,'FEATURE_RECOMPUTE_MISMATCH')
            if not np.isfinite(values).all():
                if hist_positions[day]>=max_warmup:raise MarketInputError(code,day,'MATURE_FEATURE_NONFINITE')
                reason='WARMUP'
            else:
                target_pos=positions[day]+5
                if target_pos>=len(opened) or opened[target_pos]>end:reason='UNMATURED_LABEL'
                else:
                    target=opened[target_pos]
                    if target not in quotes:reason='TARGET_SUSPENDED'
                    else:
                        label=float(quotes[target]/quotes[day]-1)
                        if not np.isfinite(label):raise MarketInputError(code,day,'INVALID_LABEL')
                        samples.append(dict(ts_code=code,trade_date=pd.Timestamp(day),label_target_date=pd.Timestamp(target),label=label,
                                            **dict(zip(names,values))))
        if reason:reasons[reason]+=1;excluded[reason].append(day)
    coverage=dict(ts_code=code,requested_open_days=len(requested),observed_rows=len(raw),eligible_rows=len(samples),
        reasons=dict(reasons),excluded_ranges={key:_ranges(days,positions) for key,days in excluded.items()},list_date=str(listed))
    return pd.DataFrame(samples,columns=('ts_code','trade_date','label_target_date','label',*names)),coverage
