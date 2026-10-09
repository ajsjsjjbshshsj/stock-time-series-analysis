"""Pure session/history/publication gates, with no provider, database or fitting."""
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import re

import numpy as np
import pandas as pd
from data_processor.adjusted_market_panel import build_model_panel, validate_contract

TZ = timezone(timedelta(hours=8))
DAILY = ['ts_code','trade_date','open','high','low','close','vol','amount']
FACTOR = ['ts_code','trade_date','adj_factor']


def iso_day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('Expected ISO date')
    date.fromisoformat(value)
    return value


def aware(value):
    if not isinstance(value,datetime) or value.utcoffset() is None:
        raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(TZ)


def moment(day, hour):
    return datetime.combine(date.fromisoformat(iso_day(day)),time(hour),TZ)


def validate_calendar(calendar):
    if (not isinstance(calendar,dict) or calendar.get('source')!='tushare.trade_cal'
            or calendar.get('exchange')!='SZSE'):
        raise ValueError('Trusted SZSE calendar required')
    start,end=iso_day(calendar['start']),iso_day(calendar['end'])
    span=(date.fromisoformat(end)-date.fromisoformat(start)).days
    rows=calendar['rows']
    if not 0<=span<=10000 or not isinstance(rows,list) or len(rows)!=span+1:
        raise ValueError('Calendar coverage incomplete')
    expected=[str(d.date()) for d in pd.date_range(start,end)]
    if ([r.get('cal_date') for r in rows]!=expected
            or any(set(r)!={'cal_date','is_open'} or type(r['is_open']) is not int or r['is_open'] not in (0,1) for r in rows)):
        raise ValueError('Calendar dates/flags invalid')
    opened=[r['cal_date'] for r in rows if r['is_open']==1]
    if not opened:
        raise ValueError('Calendar has no open sessions')
    return opened


def select_signal_date(calendar,now,requested,cutoff):
    now=aware(now)
    opened=validate_calendar(calendar)
    iso_day(cutoff)
    if not calendar['start']<=str(now.date())<=calendar['end']:
        raise ValueError('Calendar does not cover current clock')
    if requested is not None:
        iso_day(requested)
        if requested not in opened or moment(requested,16)>now:
            raise ValueError('Requested signal date is not an available completed session')
        return requested if requested>cutoff else None
    completed=[d for d in opened if moment(d,16)<=now]
    return completed[-1] if completed and completed[-1]>cutoff else None


def session_dates(calendar,signal_date):
    iso_day(signal_date)
    opened=validate_calendar(calendar)
    if signal_date not in opened:
        raise ValueError('Signal is not a calendar session')
    i=opened.index(signal_date)
    if i+5>=len(opened):
        raise ValueError('Calendar lacks T+1/T+5 coverage')
    return dict(signal_date=signal_date,entry_date=opened[i+1],exit_date=opened[i+5])


def classify_publication(signal_date,calendar,published_at,frozen_at,cutoff):
    published_at,frozen_at=aware(published_at),aware(frozen_at)
    sessions=session_dates(calendar,signal_date)
    ready=moment(signal_date,16)
    if (signal_date<=iso_day(cutoff) or ready<=frozen_at or published_at<ready or published_at<frozen_at):
        raise ValueError('Publication is not a post-freeze completed signal session')
    deadline=moment(sessions['entry_date'],9)
    eligible=published_at<deadline
    return dict(sessions,status='PROSPECTIVE_CANDIDATE' if eligible else 'LATE_DIAGNOSTIC',
                prospective_eligible=eligible,deadline=deadline.isoformat(),published_at=published_at.isoformat())


def market_frame(frame,factor=False):
    columns=FACTOR if factor else DAILY
    if frame.empty or frame.columns.duplicated().any() or not set(columns).issubset(frame):
        raise ValueError('Incomplete market columns')
    result=frame[columns].copy()
    parsed=pd.to_datetime(result.trade_date.astype(str),errors='raise')
    if parsed.dt.tz is not None or parsed.isna().any() or not parsed.equals(parsed.dt.normalize()):
        raise ValueError('Invalid market date values')
    result['trade_date']=parsed.dt.strftime('%Y-%m-%d')
    if (result[['ts_code','trade_date']].isna().any().any()
            or result.duplicated(['ts_code','trade_date']).any()
            or any(type(c) is not str or not re.fullmatch(r'\d{6}\.SZ',c) for c in result.ts_code)):
        raise ValueError('Invalid market identities')
    for name in columns[2:]:
        if result[name].map(lambda x:isinstance(x,(bool,np.bool_))).any():
            raise ValueError('Boolean market numbers are invalid')
        result[name]=pd.to_numeric(result[name],errors='raise').astype(float)
    values=result[columns[2:]].to_numpy()
    if (not np.isfinite(values).all() or (values[:,:1 if factor else 4]<=0).any()
            or (not factor and (values[:,4:]<0).any())):
        raise ValueError('Invalid market numeric values')
    return result.sort_values(['ts_code','trade_date']).reset_index(drop=True)


def frame_hash(frame):
    normalized=market_frame(frame,factor='adj_factor' in frame and 'open' not in frame)
    encoded=json.dumps(normalized.to_dict('records'),sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_history(frozen_daily,frozen_factors,daily,factors,calendar,signal_date,metadata):
    opened=validate_calendar(calendar)
    if signal_date not in opened:
        raise ValueError('History signal session absent from calendar')
    old,old_factors,current,current_factors=(market_frame(frozen_daily),market_frame(frozen_factors,True),
                                            market_frame(daily),market_frame(factors,True))
    mapping=metadata['stockid2idx']
    codes=sorted(mapping)
    starts={c:str(pd.Timestamp(d).date()) for c,d in metadata['stock_history_starts'].items()}
    if (len(codes)!=20 or sorted(mapping.values())!=list(range(20))
            or any(type(i) is not int for i in mapping.values()) or set(starts)!=set(codes)
            or starts and min(starts.values())!=str(pd.Timestamp(metadata['feature_history_start']).date())):
        raise ValueError('Invalid frozen mapping/history origins')
    for frame in (old,old_factors,current,current_factors):
        if set(frame.ts_code)!=set(codes):
            raise ValueError('History universe drift')
    cutoff=old.trade_date.max()
    if cutoff>=signal_date or old_factors.trade_date.max()!=cutoff:
        raise ValueError('Invalid frozen history cutoff')
    for frame in (current,current_factors):
        if frame.trade_date.max()>signal_date:
            raise ValueError('Future market observations rejected')
        for c in codes:
            expected=[d for d in opened if starts[c]<=d<=signal_date]
            observed=frame.loc[frame.ts_code==c,'trade_date'].tolist()
            if (not expected or expected[0]!=starts[c] or observed!=expected
                    or not calendar['start']<=starts[c]<=signal_date<=calendar['end']):
                raise ValueError('Incomplete full historical calendar prefix')
    for prefix,frame in ((old,current),(old_factors,current_factors)):
        if not frame[frame.trade_date<=cutoff].reset_index(drop=True).equals(prefix):
            raise ValueError('Frozen historical observations were revised')
    return dict(signal_date=signal_date,cutoff=cutoff,codes=codes,history_starts=starts,
                dates=[d for d in opened if min(starts.values())<=d<=signal_date],
                daily_sha256=frame_hash(current),factor_sha256=frame_hash(current_factors),
                frozen_daily_sha256=frame_hash(old),frozen_factor_sha256=frame_hash(old_factors))


def forward_model_panel(daily,factors,training_contract,provenance):
    validate_contract(training_contract)
    raw,fac=market_frame(daily),market_frame(factors,True)
    if (provenance.get('training_factor_sha256')!=training_contract['factor_snapshot_sha256']
            or provenance.get('combined_factor_sha256')!=frame_hash(fac)):
        raise ValueError('Live factor provenance differs from training basis or actual extension')
    result,contract=build_model_panel(raw,fac,mode=training_contract['mode'],
        bases=training_contract['bases'] if training_contract['mode']=='adjusted' else None,
        factor_snapshot_sha256=training_contract['factor_snapshot_sha256'])
    if contract!=training_contract:
        raise ValueError('Frozen preprocessing basis mismatch')
    result.attrs['factor_provenance']=dict(provenance)
    result.attrs['model_panel_sha256']=frame_hash(result)
    return result
