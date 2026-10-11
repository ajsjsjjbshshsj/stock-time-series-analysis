"""Pure market scope, immutable daily cohort and Shanghai due-time contracts."""
from datetime import date,datetime,time,timedelta
import re

from app.preparation.contracts import TZ,iso,identity,timestamp

SCOPE='SH_SZ_A'
VERSION='market-v1'
START='2023-01-01'
FIELDS=('ts_code','symbol','name','area','industry','market','exchange','curr_type',
        'list_status','list_date','delist_date')
SNAPSHOT_FIELDS={'schema_version','scope','trade_date','acquired_at','reference_kind',
    'source_manifest_sha256','members','codes','stock_count','member_sha256'}

def parse_run_at(value):
    if type(value) is not str or re.fullmatch(r'[0-9]{2}:[0-9]{2}',value) is None:
        raise ValueError('NIGHTLY_TIME_INVALID')
    hour,minute=map(int,value.split(':'))
    if hour>23 or minute>59 or (hour,minute)<=(16,0):raise ValueError('NIGHTLY_TIME_INVALID')
    return time(hour,minute)

def market_day(value):
    day=iso(value)
    if day<iso(START):raise ValueError('NIGHTLY_SCOPE_INVALID')
    return day

def market_identity(day):
    market_day(day)
    return identity(dict(scope=SCOPE,trade_date=day,requirements_version=VERSION))

def is_a_share(code,exchange=None):
    if type(code) is not str:return False
    valid=bool(re.fullmatch(r'(60|68)[0-9]{4}\.SH',code) or re.fullmatch(r'(0[0-9]{5}|30[0-9]{4})\.SZ',code))
    return valid and (exchange is None or exchange=={'SH':'SSE','SZ':'SZSE'}.get(code[-2:]))

def _reference_date(value,*,nullable=False):
    if nullable and value in (None,''):return None
    if type(value) is not str:raise ValueError('REFERENCE_DATE_INVALID')
    if re.fullmatch(r'[0-9]{8}',value):return datetime.strptime(value,'%Y%m%d').date().isoformat()
    return iso(value).isoformat()

def _reference(raw,acquired):
    if not isinstance(raw,dict) or set(raw)!=set(FIELDS):raise ValueError('REFERENCE_FIELDS_INVALID')
    row=dict(raw);code=row['ts_code']
    if (type(code) is not str or re.fullmatch(r'[0-9]{6}\.(SH|SZ|BJ)',code) is None
            or row['symbol']!=code[:6]
            or row['exchange']!={'SH':'SSE','SZ':'SZSE','BJ':'BSE'}[code[-2:]]):
        raise ValueError('REFERENCE_IDENTITY_INVALID')
    if (row['list_status'] not in ('L','D','P') or row['curr_type'] not in ('CNY','USD','HKD')
            or row['market'] not in ('主板','创业板','科创板','CDR','北交所')):
        raise ValueError('REFERENCE_CLASSIFICATION_INVALID')
    for field,limit in (('name',50),('area',20),('industry',30)):
        if row[field] is not None and (type(row[field]) is not str or len(row[field])>limit):
            raise ValueError('REFERENCE_FIELD_INVALID')
    row['list_date']=_reference_date(row['list_date'])
    row['delist_date']=_reference_date(row['delist_date'],nullable=True)
    if (iso(row['list_date'])>acquired.date()
            or row['delist_date'] and iso(row['delist_date'])<iso(row['list_date'])
            or row['list_status']=='D' and (row['delist_date'] is None or iso(row['delist_date'])>acquired.date())):
        raise ValueError('REFERENCE_LIFECYCLE_INVALID')
    return row

def freeze_market(day,source_rows,source_manifest_sha256,acquired_at):
    target=market_day(day);acquired=timestamp(acquired_at)
    if target>acquired.date() or type(source_manifest_sha256) is not str or re.fullmatch('[0-9a-f]{64}',source_manifest_sha256) is None:
        raise ValueError('REFERENCE_SOURCE_IDENTITY_INVALID')
    if not isinstance(source_rows,list):raise ValueError('REFERENCE_ROWS_INVALID')
    members=[];seen=set()
    for raw in source_rows:
        row=_reference(raw,acquired)
        if row['ts_code'] in seen:raise ValueError('REFERENCE_DUPLICATE')
        seen.add(row['ts_code'])
        if (is_a_share(row['ts_code'],row['exchange']) and row['curr_type']=='CNY'
                and row['market'] in ('主板','创业板','科创板','CDR')
                and iso(row['list_date'])<=target
                and (row['delist_date'] is None or target<iso(row['delist_date']))):
            members.append(row)
    members.sort(key=lambda row:row['ts_code'])
    if not members:raise ValueError('EMPTY_MARKET_UNIVERSE')
    return dict(schema_version=1,scope=SCOPE,trade_date=day,acquired_at=acquired.isoformat(),
        reference_kind='current_stock_basic_with_listing_boundaries',source_manifest_sha256=source_manifest_sha256,
        members=members,codes=[row['ts_code'] for row in members],stock_count=len(members),member_sha256=identity(members))

def validate_market(snapshot):
    if (not isinstance(snapshot,dict) or set(snapshot)!=SNAPSHOT_FIELDS
            or type(snapshot['schema_version']) is not int or snapshot['schema_version']!=1
            or type(snapshot['stock_count']) is not int or snapshot['stock_count']<=0):
        raise ValueError('MARKET_SNAPSHOT_FIELDS_INVALID')
    expected=freeze_market(snapshot['trade_date'],snapshot['members'],snapshot['source_manifest_sha256'],snapshot['acquired_at'])
    if expected!=snapshot:raise ValueError('MARKET_SNAPSHOT_HASH_OR_IDENTITY_INVALID')
    return snapshot

def due_days(calendar,now,start,run_at='20:00'):
    at=parse_run_at(run_at)
    if (not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None
            or not isinstance(calendar,dict) or calendar.get('source')!='tushare.trade_cal'):
        raise ValueError('REAL_CALENDAR_AND_AWARE_CLOCK_REQUIRED')
    local=now.astimezone(TZ);first=market_day(start)
    last=local.date() if local.time()>=at else local.date()-timedelta(days=1)
    keyed={}
    for row in calendar['rows']:
        day=iso(row['cal_date'])
        if day in keyed or type(row['is_open']) is not int or row['is_open'] not in (0,1):
            raise ValueError('CALENDAR_RESPONSE_SCHEMA')
        keyed[day]=row['is_open']
    required=[first+timedelta(days=i) for i in range(max(0,(last-first).days+1))]
    if any(day not in keyed for day in required):raise ValueError('CALENDAR_COVERAGE_REQUIRED')
    return [day.isoformat() for day in required if keyed[day]]
