"""Sealed native limit-price replies, independent of training evidence formats.

All requested codes are checked explicitly: a capped/empty day response never
proves that a missing stock has no price limits. Unknown bounds stay unknown.
"""
from datetime import datetime,timezone
import hashlib
import json
import math
from pathlib import Path
import re

FIELDS=('trade_date','ts_code','up_limit','down_limit')
CODE=re.compile(r'[0-9]{6}\.(SH|SZ|BJ)')


def _day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('EXECUTION_DAY_INVALID')
    return datetime.strptime(value,'%Y-%m-%d').date()


def _encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')


def _hash(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _request(day,code=None):
    _day(day)
    if code is not None and (type(code) is not str or not re.fullmatch(r'[0-9]{6}\.(SH|SZ)',code)):
        raise ValueError('EXECUTION_CODE_INVALID')
    params=dict(trade_date=day.replace('-',''),fields=','.join(FIELDS))
    if code is not None:params['ts_code']=code
    return dict(provider='tushare.stk_limit',day=day,params=params)


def _validate(rows,request):
    if not isinstance(rows,list) or len(rows)>5800:raise ValueError('EXECUTION_RESPONSE_BOUND')
    seen=set()
    for row in rows:
        if (not isinstance(row,dict) or set(row)!=set(FIELDS) or type(row['ts_code']) is not str
                or not CODE.fullmatch(row['ts_code']) or row['ts_code'] in seen
                or row['trade_date']!=request['params']['trade_date']
                or 'ts_code' in request['params'] and row['ts_code']!=request['params']['ts_code']):
            raise ValueError('EXECUTION_RESPONSE_IDENTITY')
        seen.add(row['ts_code'])
        for name in ('up_limit','down_limit'):
            value=row[name]
            if value is not None and (isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value) or value<0):
                raise ValueError('EXECUTION_BOUND_INVALID')
        if known(row) and row['down_limit']>row['up_limit']:raise ValueError('EXECUTION_BOUND_ORDER')
    return rows


def known(row):
    return all(row[name] is not None and row[name]>0 for name in ('up_limit','down_limit'))


class ExecutionStore:
    """A caller-configured root; no developer paths, SQL writes or SDK imports."""
    def __init__(self,root):
        self.root=Path(root).absolute()

    def _directory(self,day):
        _day(day);path=self.root/'execution_limits'/day
        if any(p.is_symlink() or bool(getattr(p.lstat(),'st_file_attributes',0)&0x400)
               for p in (path,*path.parents) if p.exists()):raise ValueError('EXECUTION_PATH_UNSAFE')
        return path

    def _load(self,path):
        if path.stat().st_size>4*1024*1024:raise ValueError('EXECUTION_SOURCE_TOO_LARGE')
        body=json.loads(path.read_text(encoding='utf-8'))
        receipt=path.with_suffix('.complete.json')
        if not receipt.exists() or json.loads(receipt.read_text())!=dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest()):
            raise ValueError('EXECUTION_SOURCE_INCOMPLETE')
        if set(body)!={'schema_version','request','acquired_at','rows'} or body['schema_version']!=1:
            raise ValueError('EXECUTION_SOURCE_SCHEMA')
        request=body['request'];expected=_request(request['day'],request['params'].get('ts_code'))
        if request!=expected or path.stem!=_hash(request) or path.parent.name!=request['day']:
            raise ValueError('EXECUTION_SOURCE_BINDING')
        acquired=datetime.fromisoformat(body['acquired_at'])
        if acquired.utcoffset() is None or acquired.date()<_day(request['day']):raise ValueError('EXECUTION_SOURCE_TIME')
        _validate(body['rows'],request)
        return body

    def has_request(self,day,code=None):
        path=self._directory(day)/(_hash(_request(day,code))+'.json')
        if path.exists():self._load(path);return True
        if path.with_suffix('.complete.json').exists():raise ValueError('EXECUTION_ORPHAN_RECEIPT')
        return False

    def save(self,day,rows,code=None):
        request=_request(day,code);_validate(rows,request)
        directory=self._directory(day);directory.mkdir(parents=True,exist_ok=True)
        path=directory/(_hash(request)+'.json')
        body=dict(schema_version=1,request=request,acquired_at=datetime.now(timezone.utc).isoformat(),rows=rows)
        with path.open('xb') as f:f.write(_encoded(body))
        with path.with_suffix('.complete.json').open('xb') as f:f.write(_encoded(dict(sha256=hashlib.sha256(path.read_bytes()).hexdigest())))

    def load(self,day):
        directory=self._directory(day)
        if not directory.exists():return []
        merged={}
        for path in sorted(directory.glob('*.json')):
            if path.name.endswith('.complete.json'):
                if not path.with_name(path.name.replace('.complete.json','.json')).exists():raise ValueError('EXECUTION_ORPHAN_RECEIPT')
                continue
            if path.is_symlink():raise ValueError('EXECUTION_PATH_UNSAFE')
            for row in self._load(path)['rows']:
                code=row['ts_code']
                if code in merged and merged[code]!=row:raise ValueError('EXECUTION_SOURCE_CONFLICT')
                merged[code]=row
        return [dict(row,trade_date=day) for _,row in sorted(merged.items())]


def acquire_limits(day,codes,client,pacing,store):
    """Resume a date, requesting only uncovered stocks after one broad reply.

    pacing is the collector's existing shared Requests limiter. Capped broad
    replies are acceptable only for the explicitly present required codes.
    """
    codes=sorted(set(codes))
    for code in codes:_request(day,code)
    def missing():
        present={r['ts_code'] for r in store.load(day) if known(r)}
        return sorted(set(codes)-present)
    def collect(code=None):
        if store.has_request(day,code):return
        request=_request(day,code)
        frame=pacing.call(client.stk_limit,request['params'])
        if frame is None or frame.columns.duplicated().any() or set(frame)!=set(FIELDS):
            raise ValueError('EXECUTION_RESPONSE_SCHEMA')
        rows=frame.astype(object).where(frame.notna(),None).to_dict('records')
        store.save(day,rows,code)
    if missing():
        # An almost-complete day must not trigger a market-wide re-query.
        if len(missing())>=100:collect()
        for code in missing():collect(code)
    return dict(day=day,missing_codes=missing())
