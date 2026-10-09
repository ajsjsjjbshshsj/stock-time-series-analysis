"""Pure, immutable inventory and request planning; never reads credentials."""
from datetime import date, datetime, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

from app.market_data.forward_snapshot import read, write, digest

PROJECT = Path(__file__).resolve().parents[4]
DAILY = ['ts_code','trade_date','open','high','low','close','pre_close','change','pct_chg','vol','amount']
BASIC = ['ts_code','trade_date','turnover_rate','pe','pe_ttm','pb','ps','total_mv']
FACTOR = ['ts_code','trade_date','adj_factor']
SUSPEND = ['ts_code','trade_date','suspend_timing','suspend_type']


def canonical(value):
    """Preserve SQL decimals/nulls exactly rather than round-trip through floats."""
    if isinstance(value, Decimal):
        if not value.is_finite(): raise ValueError('Nonfinite database decimal')
        return str(value)
    if isinstance(value, (date,datetime)): return value.isoformat()
    if isinstance(value, dict): return {k:canonical(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [canonical(v) for v in value]
    return value


def identity(value):
    return hashlib.sha256(json.dumps(canonical(value),ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def iso(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):
        raise ValueError('ISO date required')
    return date.fromisoformat(value)


def verify_pool(path):
    # Reuse the existing configuration-free verifier, not app imports which collide
    # between the Collector and analysis services. Path is fixed trusted repo code.
    spec=importlib.util.spec_from_file_location('_repair_pool_verifier',
        PROJECT/'python-services/stock-analysis-app/analysis/csi300_universe.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.verify_universe(Path(path))


def validate_inputs(pool,calendar,tables):
    codes=pool['codes']; start,end=iso(tables['start']),iso(tables['end'])
    if len(codes)!=300 or codes!=sorted(set(codes)) or start>end: raise ValueError('Invalid pool/range')
    if calendar.get('source')!='tushare.trade_cal': raise ValueError('Verified trading calendar required')
    rows=calendar['rows']; keyed={r['cal_date']:r['is_open'] for r in rows}
    if len(keyed)!=len(rows) or any(type(v) is not int or v not in (0,1) for v in keyed.values()):
        raise ValueError('Invalid calendar rows')
    for key in keyed: iso(key)
    if iso(calendar['start'])>start or iso(calendar['end'])<end: raise ValueError('Calendar range incomplete')
    days=[(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)]
    if not set(days)<=set(keyed): raise ValueError('Calendar coverage incomplete')
    stocks=tables['stock_basic']; listed={r['ts_code']:r['list_date'] for r in stocks}
    if len(listed)!=len(stocks) or set(listed)!=set(codes): raise ValueError('Missing/duplicate IPO identity')
    for day in listed.values(): iso(day)
    for table in ('stock_daily','stock_daily_basic'):
        schema=tables['schema'][table]
        if (['ts_code','trade_date'] not in schema['unique_keys']
            or not {'id','ts_code','trade_date'}<=set(schema['columns'])
            or set(schema['columns'])!=set(schema['types'])): raise ValueError('Invalid table schema')
        keys=set()
        for row in tables[table]:
            key=(row['ts_code'],row['trade_date'])
            if (key in keys or key[0] not in listed or not start<=iso(key[1])<=end
                or iso(key[1])<iso(listed[key[0]]) or keyed[key[1]]!=1
                or set(row)!=set(schema['columns'])): raise ValueError('Invalid/duplicate original table row')
            keys.add(key)


def write_baseline(output: Path,pool: Path,calendar: dict,tables: dict) -> dict:
    output,pool=Path(output),Path(pool); manifest=verify_pool(pool)
    tables=canonical(tables); calendar=canonical(calendar)
    validate_inputs(manifest,calendar,tables)
    binding=dict(pool_sha256=digest(pool/'manifest.json'),calendar_sha256=identity(calendar),tables_sha256=identity(tables))
    if output.exists():
        old=verify_baseline(output)
        if old['binding']!=binding: raise ValueError('Existing baseline identity differs')
        return old
    output.mkdir(parents=True,exist_ok=False)
    target=output/'pool'/manifest['pool_version']; target.parent.mkdir()
    shutil.copytree(pool,target)
    write(output/'calendar.json',calendar,exclusive=True)
    write(output/'tables.json',tables,exclusive=True)
    summary=dict(schema_version=1,pool_version=manifest['pool_version'],start=tables['start'],end=tables['end'],
        binding=binding,files={n:digest(output/n) for n in ('calendar.json','tables.json')})
    write(output/'manifest.json',summary,exclusive=True)
    verify_baseline(output,require_complete=False)
    write(output/'complete.json',dict(manifest_sha256=digest(output/'manifest.json')),exclusive=True)
    return verify_baseline(output)


def verify_baseline(path: Path,require_complete: bool=True) -> dict:
    path=Path(path)
    try:
        manifest=read(path/'manifest.json')
        if require_complete and read(path/'complete.json')!=dict(manifest_sha256=digest(path/'manifest.json')):
            raise ValueError('Baseline completion hash mismatch')
        if manifest['schema_version']!=1 or manifest['files']!={n:digest(path/n) for n in ('calendar.json','tables.json')}:
            raise ValueError('Baseline file hash mismatch')
        poolpath=path/'pool'/manifest['pool_version']; pool=verify_pool(poolpath)
        cal,tables=read(path/'calendar.json'),read(path/'tables.json')
        validate_inputs(pool,cal,tables)
        if (manifest['binding']!=dict(pool_sha256=digest(poolpath/'manifest.json'),calendar_sha256=identity(cal),tables_sha256=identity(tables))
            or (manifest['start'],manifest['end'])!=(tables['start'],tables['end'])):
            raise ValueError('Baseline binding differs')
        return manifest
    except (OSError,KeyError,TypeError) as exc:
        raise ValueError('Partial/invalid repair baseline') from exc


def load_baseline(path: Path) -> dict:
    manifest=verify_baseline(path); path=Path(path)
    return dict(manifest=manifest,pool=verify_pool(path/'pool'/manifest['pool_version']),
                calendar=read(path/'calendar.json'),tables=read(path/'tables.json'))


def request(method,codes,start,end):
    fields={'daily':DAILY,'daily_basic':BASIC,'adj_factor':FACTOR,'suspend_d':SUSPEND}[method]
    if method in ('daily','daily_basic','suspend_d'):
        params=dict(trade_date=start.replace('-',''),fields=','.join(fields))
        if method!='daily_basic': params['ts_code']=','.join(codes)
    else:
        params=dict(ts_code=codes[0],start_date=start.replace('-',''),end_date=end.replace('-',''),fields=','.join(fields))
    r=dict(method=method,params=params,expected_codes=codes,start=start,end=end,fields=fields,
           capacity={'daily':6000,'daily_basic':6000,'suspend_d':5000,'adj_factor':None}[method])
    return dict(r,id=identity(r))


def plan_requests(baseline: Path,additional_daily: list[dict] | None=None) -> list[dict]:
    data=load_baseline(baseline); t=data['tables']; codes=data['pool']['codes']
    listed={r['ts_code']:r['list_date'] for r in t['stock_basic']}
    dates=sorted(r['cal_date'] for r in data['calendar']['rows'] if r['is_open']==1 and t['start']<=r['cal_date']<=t['end'])
    daily={(r['ts_code'],r['trade_date']) for r in t['stock_daily']}
    basic={(r['ts_code'],r['trade_date']) for r in t['stock_daily_basic']}
    extra={(r['ts_code'],r['trade_date']) for r in (additional_daily or [])}
    if any(c not in codes or d not in dates or d<listed[c] for c,d in extra): raise ValueError('Additional daily outside baseline')
    quote_keys=daily|extra  # once, not a190000-key copy per stock-day
    result=[]
    for day in dates:
        missing=[c for c in codes if listed[c]<=day and (c,day) not in daily]
        if missing:
            result.extend([request('daily',missing,day,day),request('suspend_d',missing,day,day)])
        missing_basic=[c for c in codes if (c,day) in quote_keys and (c,day) not in basic]
        if missing_basic: result.append(request('daily_basic',missing_basic,day,day))
    for code in codes:
        start=max(iso(t['start']),iso(listed[code])); end=iso(t['end'])
        while start<=end:
            stop=min(date(start.year,12,31),end)
            result.append(request('adj_factor',[code],start.isoformat(),stop.isoformat()))
            start=stop+timedelta(days=1)
    return result
