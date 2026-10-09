"""Bounded raw market repair requests using the canonical shared quota lock."""
from datetime import datetime
from pathlib import Path
import math
import re

import pandas as pd

from app.market_data.forward_snapshot import Requests, TZ, read, write, digest
from app.market_data.csi300_constituent_snapshot import migrate_request_state
from app.market_data.repair_contract import identity, iso


def validate_request(r):
    if (r['method'] not in ('daily','daily_basic','adj_factor','suspend_d')
        or r['id']!=identity({k:v for k,v in r.items() if k!='id'})
        or r['expected_codes']!=sorted(set(r['expected_codes'])) or not r['expected_codes']
        or any(not re.fullmatch(r'\d{6}\.(SH|SZ)',c) for c in r['expected_codes'])
        or iso(r['start'])>iso(r['end']) or r['params']['fields']!=','.join(r['fields'])):
        raise ValueError('Invalid repair request contract')


def validate_rows(rows,r):
    if r['capacity'] is not None and len(rows)>=r['capacity']:
        raise ValueError('SCHEMA: capacity reached; response may be truncated')
    keys=set()
    for row in rows:
        if set(row)!=set(r['fields']): raise ValueError('SCHEMA: response fields differ')
        code,day=row['ts_code'],row['trade_date']
        if (not isinstance(code,str) or not re.fullmatch(r'\d{6}\.(SH|SZ|BJ)',code)
            or not isinstance(day,str) or not re.fullmatch(r'\d{8}',day)):
            raise ValueError('SCHEMA: invalid identity/date')
        date=datetime.strptime(day,'%Y%m%d').date().isoformat()
        if not r['start']<=date<=r['end'] or (r['method']!='daily_basic' and code not in r['expected_codes']):
            raise ValueError('SCHEMA: request boundary mismatch')
        key=(code,day,row.get('suspend_type'),row.get('suspend_timing')) if r['method']=='suspend_d' else (code,day)
        if key in keys: raise ValueError('SCHEMA: duplicate response')
        keys.add(key)
        if r['method']=='suspend_d':
            if row['suspend_type'] not in ('S','R') or (row['suspend_timing'] is not None and not isinstance(row['suspend_timing'],str)):
                raise ValueError('SCHEMA: invalid suspension record')
            continue
        for field in r['fields'][2:]:
            value=row[field]
            if value is None and r['method']=='daily_basic': continue
            if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):
                raise ValueError('SCHEMA: invalid numeric field')
            if field in ('open','high','low','close','pre_close','adj_factor') and value<=0:
                raise ValueError('SCHEMA: positive price/factor required')
            if field in ('vol','amount','turnover_rate','total_mv') and value<0:
                raise ValueError('SCHEMA: nonnegative amount required')
        if r['method']=='daily' and not row['low']<=min(row['open'],row['close'])<=max(row['open'],row['close'])<=row['high']:
            raise ValueError('SCHEMA: OHLC range invalid')


def _load_shard(output,r):
    file=output/'shards'/f"{r['id']}.json"
    proof=read(file.with_suffix('.proof.json'))
    if proof!=dict(sha256=digest(file),raw_sha256=digest(file.with_suffix('.parquet'))):
        raise ValueError('Shard hash invalid')
    data=read(file)
    if data['request']!=r or data['row_count']!=len(data['rows']): raise ValueError('Shard request/count differs')
    stamp=datetime.fromisoformat(data['acquired_at'])
    if stamp.utcoffset()!=TZ.utcoffset(None): raise ValueError('Invalid source timestamp')
    validate_rows(data['rows'],r)
    raw=pd.read_parquet(file.with_suffix('.parquet'))
    if raw.columns.duplicated().any() or list(raw.columns)!=r['fields'] or _rows(raw)!=data['rows']:
        raise ValueError('Original response disagrees with normalized proof')
    return data


def _rows(frame):
    # SDK native NaNs become JSON null, not invented numeric values.
    return frame.astype(object).where(frame.notna(),None).to_dict('records')


def verify_acquisition(output: Path,requests: list[dict],require_complete: bool=True) -> dict:
    output=Path(output)
    try:
        if read(output/'binding.json')!=dict(schema_version=1,requests=requests): raise ValueError('Acquisition binding differs')
        if len({r['id'] for r in requests})!=len(requests): raise ValueError('Duplicate request ID')
        shards=[]; missing=[]
        for r in requests:
            validate_request(r)
            file=output/'shards'/f"{r['id']}.json"
            if not file.exists() and not file.with_suffix('.proof.json').exists():
                missing.append(r['id']); continue
            shards.append(_load_shard(output,r))
        if require_complete:
            manifest=read(output/'manifest.json')
            expected=dict(schema_version=1,binding_sha256=digest(output/'binding.json'),
                files={p.name:digest(p) for p in sorted((output/'shards').iterdir()) if p.is_file()})
            if missing or manifest!=expected or read(output/'complete.json')!=dict(manifest_sha256=digest(output/'manifest.json')):
                raise ValueError('Acquisition incomplete/hash mismatch')
        return dict(complete=not missing,shards=shards,missing=missing)
    except (OSError,KeyError,TypeError) as exc:
        raise ValueError('Partial/invalid market acquisition') from exc


def collect_requests(client: object,requests: list[dict],output: Path,request_state: Path,
                     interval: float,clock: callable,sleep: callable) -> dict:
    output=Path(output)
    for r in requests: validate_request(r)
    if len({r['id'] for r in requests})!=len(requests): raise ValueError('Duplicate request ID')
    binding=dict(schema_version=1,requests=requests)
    if (output/'complete.json').exists(): return verify_acquisition(output,requests)
    output.mkdir(parents=True,exist_ok=True); (output/'shards').mkdir(exist_ok=True)
    if (output/'binding.json').exists():
        if read(output/'binding.json')!=binding: raise ValueError('Partial acquisition identity differs')
    else: write(output/'binding.json',binding,exclusive=True)
    state=None
    for r in requests:
        file=output/'shards'/f"{r['id']}.json"
        if file.exists() or file.with_suffix('.proof.json').exists():
            _load_shard(output,r); continue
        if file.with_suffix('.parquet').exists(): raise ValueError('Unverified raw response retained; use a new acquisition directory')
        try:
            if state is None:
                migrate_request_state(request_state)
                state=Requests(output,interval,clock,sleep,request_state)
            if client is None: raise RuntimeError('ACQUISITION_CLIENT_REQUIRED')
            response=state.call(getattr(client,r['method']),r['params'])
        except RuntimeError as exc:
            # Only our own failure vocabulary, never arbitrary provider text.
            status=state.state.get('failure','BLOCKED') if state else 'BLOCKED'
            report=dict(complete=False,status=status,model_ready=False)
            write(output/'report.json',report)
            return report
        try:
            if not isinstance(response,pd.DataFrame) or response.columns.duplicated().any() or set(response.columns)!=set(r['fields']):
                raise ValueError('SCHEMA: invalid response fields')
            frame=response[r['fields']].copy()
            frame.to_parquet(file.with_suffix('.parquet'),index=False)
            rows=_rows(frame); validate_rows(rows,r)
            data=dict(request=r,acquired_at=datetime.fromtimestamp(clock(),TZ).isoformat(),row_count=len(rows),rows=rows)
            write(file,data,exclusive=True)
            write(file.with_suffix('.proof.json'),dict(sha256=digest(file),raw_sha256=digest(file.with_suffix('.parquet'))),exclusive=True)
        except (ValueError,TypeError,KeyError):
            report=dict(complete=False,status='SCHEMA',model_ready=False)
            write(output/'report.json',report); return report
    verify_acquisition(output,requests,require_complete=False)
    manifest=dict(schema_version=1,binding_sha256=digest(output/'binding.json'),
        files={p.name:digest(p) for p in sorted((output/'shards').iterdir()) if p.is_file()})
    write(output/'manifest.json',manifest,exclusive=True)
    # Verify all proofs before publishing completion (no provider/config imports).
    verify_acquisition(output,requests,require_complete=False)
    write(output/'complete.json',dict(manifest_sha256=digest(output/'manifest.json')),exclusive=True)
    return verify_acquisition(output,requests)
