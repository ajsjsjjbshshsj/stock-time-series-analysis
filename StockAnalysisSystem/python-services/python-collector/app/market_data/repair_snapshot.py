"""Offline source attribution and immutable repair packages; no SDK/SQL calls."""
from collections import Counter
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import re
import shutil

import pandas as pd

from app.market_data.forward_snapshot import read, write, digest
from app.market_data.repair_contract import load_baseline, plan_requests, iso, DAILY, BASIC, FACTOR, SUSPEND
from app.market_data.repair_acquisition import verify_acquisition


def key(row): return row['ts_code'],row['trade_date']


def sql_value(value,type_name):
    """Match actual MySQL DECIMAL precision without changing raw source evidence."""
    if value is None: return None
    if isinstance(value,datetime): return value.isoformat()
    if isinstance(value,date): return value.isoformat()
    match=re.fullmatch(r'decimal\((\d+),(\d+)\)',type_name.lower())
    if match:
        precision,scale=map(int,match.groups()); number=Decimal(str(value))
        if not number.is_finite(): raise ValueError('Invalid SQL decimal')
        number=number.quantize(Decimal(1).scaleb(-scale),rounding=ROUND_HALF_UP)
        if abs(number)>=Decimal(10)**(precision-scale): raise ValueError('SQL decimal overflow')
        return str(number)
    return str(value) if isinstance(value,(float,Decimal)) else value


def equal_business(old,new,fields,types):
    return all(sql_value(old.get(f),types.get(f,''))==sql_value(new.get(f),types.get(f,'')) for f in fields)


def classify_sources(data: dict,sources: dict[str,list[dict]]) -> dict:
    t=data['tables']; codes=data['pool']['codes']; listed={r['ts_code']:r['list_date'] for r in t['stock_basic']}
    dates=sorted(r['cal_date'] for r in data['calendar']['rows'] if r['is_open']==1 and t['start']<=r['cal_date']<=t['end'])
    normalized={}
    for method,rows in sources.items():
        normalized[method]=[]
        for item in rows:
            r=dict(item); raw=str(r['trade_date'])
            r['trade_date']=f'{raw[:4]}-{raw[4:6]}-{raw[6:]}' if len(raw)==8 else raw
            iso(r['trade_date'])
            if r['ts_code'] not in codes:
                if method=='daily_basic': continue  # original full-market response remains in evidence
                raise ValueError('Source code outside pool')
            if r['trade_date'] not in dates or r['trade_date']<listed[r['ts_code']]: raise ValueError('Source outside listing/calendar')
            normalized[method].append(r)
    conflicts=[]; candidates={}; combined={}; origins={}
    for table,method,fields in [('stock_daily','daily',DAILY),('stock_daily_basic','daily_basic',BASIC)]:
        old={key(r):dict(r) for r in t[table]}; merged=dict(old); origin={k:'DATABASE_ROW' for k in old}; new=[]
        seen=set(); types=t['schema'][table]['types']
        for raw in normalized[method]:
            k=key(raw)
            if k in seen: raise ValueError('Duplicate candidate source key')
            seen.add(k)
            candidate={f:sql_value(raw[f],types.get(f,'')) for f in fields}
            if method=='daily_basic': candidate['source']='TUSHARE'
            if k in old:
                if not equal_business(old[k],candidate,fields,types):
                    conflicts.append(dict(table=table,ts_code=k[0],trade_date=k[1],old=old[k],candidate=candidate))
                    origin[k]='SOURCE_CONFLICT'
                else: origin[k]='PROVIDER_VERIFIED_EXISTING'
            else:
                merged[k]=candidate; origin[k]='PROVIDER_ROW'; new.append(candidate)
        candidates[table]=sorted(new,key=key); combined[method]=[merged[k] for k in sorted(merged)]; origins[method]=origin
    factor={}; suspend={}
    for r in normalized['adj_factor']:
        if key(r) in factor: raise ValueError('Duplicate factor key')
        factor[key(r)]=r
    for r in normalized['suspend_d']: suspend.setdefault(key(r),[]).append(r)
    daily={key(r):r for r in combined['daily']}; basic={key(r):r for r in combined['daily_basic']}
    statuses=[]
    for day in dates:
        for code in codes:
            k=(code,day); events=suspend.get(k,[])
            full=bool(events) and all(e['suspend_type']=='S' and e['suspend_timing'] is None for e in events)
            ambiguous=any(e['suspend_type']=='S' for e in events) and any(e['suspend_type']=='R' for e in events)
            suspension_conflict=ambiguous or (full and k in daily)
            if suspension_conflict:
                conflicts.append(dict(table='stock_daily',ts_code=code,trade_date=day,
                    reason='QUOTE_SUSPENSION_CONFLICT',quote=daily.get(k),suspensions=events))
            ds=('PRE_LISTING' if day<listed[code] else 'SOURCE_CONFLICT' if suspension_conflict else
                origins['daily'][k] if k in daily else 'FULL_DAY_SUSPENSION' if full else 'UNRESOLVED_MISSING')
            bs=origins['daily_basic'].get(k,'PRE_LISTING' if day<listed[code] else 'MISSING_BASIC_ROW')
            row=dict(ts_code=code,trade_date=day,daily_status=ds,basic_status=bs,
                     factor_status='PRE_LISTING' if day<listed[code] else 'PROVIDER_ROW' if k in factor else 'MISSING_FACTOR')
            for field in ('pe','pe_ttm'):
                value=basic.get(k,{}).get(field)
                row[field+'_status']=('NO_ROW' if k not in basic else 'SOURCE_CONFLICT' if bs=='SOURCE_CONFLICT' else
                    'VALUE' if value is not None else 'PROVIDER_NULL' if bs in ('PROVIDER_ROW','PROVIDER_VERIFIED_EXISTING') else 'DATABASE_NULL_UNVERIFIED')
            statuses.append(row)
    # Cross-source conflicts are known only after quote/suspension attribution.
    # Preserve raw evidence and merged source rows, but never import these keys.
    blocked={(r['ts_code'],r['trade_date']) for r in statuses if r['daily_status']=='SOURCE_CONFLICT'}
    for table in candidates: candidates[table]=[r for r in candidates[table] if key(r) not in blocked]
    counts=Counter(r['daily_status'] for r in statuses)
    issues={k:counts[k] for k in ('UNRESOLVED_MISSING','SOURCE_CONFLICT')}
    issues.update(MISSING_BASIC_ROW=sum(r['basic_status']=='MISSING_BASIC_ROW' and r['daily_status'] not in ('PRE_LISTING','FULL_DAY_SUSPENSION') for r in statuses),
                  MISSING_FACTOR=sum(r['factor_status']=='MISSING_FACTOR' for r in statuses),EXISTING_KEY_CONFLICT=sum('old' in r for r in conflicts))
    report=dict(pool_version=data['pool']['pool_version'],stock_count=300,start=t['start'],end=t['end'],
        acquisition_complete=True,source_issue_counts=issues,status_counts=dict(counts),model_ready=False,
        daily_rows=len(daily),basic_rows=len(basic),factor_rows=len(factor),
        insert_candidates={table:len(rows) for table,rows in candidates.items()})
    return dict(daily=combined['daily'],basic=combined['daily_basic'],factors=[factor[k] for k in sorted(factor)],
        suspensions=sorted(normalized['suspend_d'],key=lambda r:(*key(r),r['suspend_type'],r['suspend_timing'] or '')),
        status=statuses,conflicts=conflicts,candidates=candidates,report=report)


def _sources(base,acq):
    requests=plan_requests(base); first=verify_acquisition(acq/'initial',requests)
    rows={name:[] for name in ('daily','daily_basic','adj_factor','suspend_d')}
    for s in first['shards']: rows[s['request']['method']].extend(s['rows'])
    extra=[dict(r,trade_date=f"{r['trade_date'][:4]}-{r['trade_date'][4:6]}-{r['trade_date'][6:]}") for r in rows['daily']]
    known={r['id'] for r in requests}
    additional=[r for r in plan_requests(base,extra) if r['method']=='daily_basic' and r['id'] not in known]
    # If a date already had a basic request, its complete raw all-market response
    # covers new quotes too; no second same-day provider response is needed.
    queried_dates={r['start'] for r in requests if r['method']=='daily_basic'}
    additional=[r for r in additional if r['start'] not in queried_dates]
    second=verify_acquisition(acq/'basic',additional)
    for s in second['shards']: rows['daily_basic'].extend(s['rows'])
    return rows


def _tree_hashes(path):
    return {p.relative_to(path).as_posix():digest(p) for p in sorted(path.rglob('*')) if p.is_file()}


def output_columns(data,result):
    return dict(daily=list(data['tables']['schema']['stock_daily']['columns']),
        basic=list(data['tables']['schema']['stock_daily_basic']['columns']),factors=FACTOR,
        suspensions=SUSPEND,status=list(result['status'][0]))


def assemble_snapshot(baseline: Path,acquisition: Path,output: Path) -> dict:
    base,acq,output=map(Path,(baseline,acquisition,output))
    data=load_baseline(base); sources=_sources(base,acq); expected=classify_sources(data,sources)
    binding=dict(baseline_sha256=digest(base/'manifest.json'),acquisition_sha256={phase:digest(acq/phase/'manifest.json') for phase in ('initial','basic')})
    if output.exists():
        previous=verify_snapshot(output)
        if previous['manifest']['binding']!=binding: raise ValueError('Existing snapshot identity differs')
        return previous
    output.mkdir(parents=True,exist_ok=False); evidence=output/'evidence'; evidence.mkdir()
    shutil.copytree(base,evidence/'baseline')
    for phase in ('initial','basic'):
        dest=evidence/'acquisition'/phase; dest.mkdir(parents=True)
        for name in ('binding.json','manifest.json','complete.json'): shutil.copyfile(acq/phase/name,dest/name)
        shutil.copytree(acq/phase/'shards',dest/'shards')
    for name,columns in output_columns(data,expected).items():
        pd.DataFrame(expected[name],columns=columns).to_parquet(output/(name+'.parquet'),index=False)
    for name in ('conflicts','candidates','report'): write(output/(name+'.json'),expected[name],exclusive=True)
    files={p.relative_to(output).as_posix():digest(p) for p in sorted(output.rglob('*')) if p.is_file()}
    write(output/'manifest.json',dict(schema_version=1,binding=binding,files=files),exclusive=True)
    verify_snapshot(output,require_complete=False)
    write(output/'complete.json',dict(manifest_sha256=digest(output/'manifest.json')),exclusive=True)
    return verify_snapshot(output)


def verify_snapshot(path: Path,require_complete: bool=True) -> dict:
    path=Path(path)
    try:
        manifest=read(path/'manifest.json')
        if require_complete and read(path/'complete.json')!=dict(manifest_sha256=digest(path/'manifest.json')): raise ValueError('Snapshot completion invalid')
        actual={k:v for k,v in _tree_hashes(path).items() if k not in ('manifest.json','complete.json')}
        if manifest['schema_version']!=1 or manifest['files']!=actual: raise ValueError('Snapshot SHA invalid')
        base,acq=path/'evidence/baseline',path/'evidence/acquisition'
        if manifest['binding']!=dict(baseline_sha256=digest(base/'manifest.json'),acquisition_sha256={phase:digest(acq/phase/'manifest.json') for phase in ('initial','basic')}):
            raise ValueError('Snapshot upstream binding invalid')
        data=load_baseline(base); expected=classify_sources(data,_sources(base,acq))
        for name in ('conflicts','candidates','report'):
            if read(path/(name+'.json'))!=expected[name]: raise ValueError('Snapshot attribution/report differs')
        for name,columns in output_columns(data,expected).items():
            actual=pd.read_parquet(path/(name+'.parquet'))
            if list(actual.columns)!=columns or actual.columns.duplicated().any():
                raise ValueError('Snapshot independent column contract differs')
            want=pd.DataFrame(expected[name],columns=columns)
            if not actual.equals(want): raise ValueError('Snapshot rows differ from original evidence')
        return dict(manifest=manifest,report=expected['report'])
    except (OSError,KeyError,TypeError) as exc:
        raise ValueError('Partial/invalid market repair snapshot') from exc


def import_candidates(snapshot: Path) -> dict[str,list[dict]]:
    verify_snapshot(snapshot)
    return read(Path(snapshot)/'candidates.json')
