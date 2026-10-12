"""Bounded raw CSI300 monthly evidence, no database or model mutation."""
from datetime import datetime, timedelta
from pathlib import Path
import re
import time
import os
import uuid

import numpy as np
import pandas as pd
from app.market_data.forward_snapshot import Requests, TZ, day, read, write, digest

INDEX = '000300.SH'
FIELDS = ['index_code', 'con_code', 'trade_date', 'weight']
PROJECT = Path(__file__).resolve().parents[4]


def migrate_request_state(request_state, *, legacy_root=None):
    """Merge deadline maxima under exactly the lock used by Requests.call."""
    target = Path(request_state).resolve()
    if legacy_root is None and target == (PROJECT/'.runtime/market_requests/request_state.json').resolve():
        legacy_root = PROJECT/'python-services/stock-analysis-app/models/transformer/forward_signals_20261008'
    if legacy_root is None: return
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.with_suffix('.lock')
    try: fd = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError: raise RuntimeError('REQUEST_STATE_BUSY: existing shared lock retained') from None
    try:
        os.write(fd,str(os.getpid()).encode('ascii'))
        current = read(target) if target.exists() else {}
        old = Path(legacy_root)
        states = [current]+[read(p) for p in [old/'request_state.json',*old.rglob('acquisition.json')] if p.is_file()]
        merged = dict(current)
        for key in ('retry_not_before','last_request_at'):
            values = [s[key] for s in states if key in s]
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not np.isfinite(v) or v<0 for v in values):
                raise ValueError('Invalid legacy/shared request deadline')
            if values: merged[key] = max(values)
        if not target.exists() or merged != current:
            tmp = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            write(tmp,merged,exclusive=True)
            os.replace(tmp,target)
    finally:
        os.close(fd)
        lock.unlink()


def month_requests(requested_as_of):
    stamp = pd.Timestamp(day(requested_as_of))
    result = []
    for offset in range(3):
        start = (stamp-pd.DateOffset(months=offset)).replace(day=1)
        end = min(start+pd.offsets.MonthEnd(0), stamp)
        result.append(dict(index_code=INDEX, start_date=start.strftime('%Y%m%d'), end_date=end.strftime('%Y%m%d')))
    return result


def valid_rows(rows, params):
    if not isinstance(rows, list): raise ValueError('SCHEMA: response rows required')
    keys = set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != set(FIELDS) or row['index_code'] != INDEX
                or type(row['con_code']) is not str or not re.fullmatch(r'\d{6}\.(SH|SZ)', row['con_code'])
                or type(row['trade_date']) is not str or not re.fullmatch(r'\d{8}', row['trade_date'])
                or not params['start_date'] <= row['trade_date'] <= params['end_date']):
            raise ValueError('SCHEMA: invalid constituent identity/date')
        datetime.strptime(row['trade_date'], '%Y%m%d')
        weight = row['weight']
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not np.isfinite(weight) or weight < 0:
            raise ValueError('SCHEMA: invalid original weight')
        key = row['trade_date'], row['con_code']
        if key in keys: raise ValueError('SCHEMA: duplicate constituent')
        keys.add(key)
    return rows


def select_rows(rows):
    if not rows: return None, []
    latest = max(r['trade_date'] for r in rows)
    selected = [r for r in rows if r['trade_date'] == latest]
    if len(selected) != 300 or len({r['con_code'] for r in selected}) != 300:
        raise ValueError('SCHEMA: latest snapshot must have exactly300 unique stocks')
    return datetime.strptime(latest, '%Y%m%d').date().isoformat(), selected


def verify_constituent_evidence(output, requested_as_of=None, *, require_complete=True):
    root = Path(output)
    manifest = read(root/'acquisition_manifest.json')
    if require_complete and read(root/'complete.json') != dict(schema_version=1, manifest_sha256=digest(root/'acquisition_manifest.json')):
        raise ValueError('Acquisition completion hash mismatch')
    requested = manifest['requested_as_of']
    if requested_as_of is not None and requested != requested_as_of:
        raise ValueError('Source request identity mismatch')
    if read(root/'binding.json') != dict(schema_version=1, source='tushare.index_weight', index_code=INDEX, requested_as_of=requested):
        raise ValueError('Source binding mismatch')
    expected = month_requests(requested)
    pages = manifest['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= 3:
        raise ValueError('Invalid source request count')
    chosen_day, chosen_rows = None, []
    for i, entry in enumerate(pages):
        name = 'request_'+expected[i]['start_date'][:6]+'.json'
        if entry['path'] != name or entry['params'] != expected[i] or entry['sha256'] != digest(root/name):
            raise ValueError('Raw response identity/hash mismatch')
        response = read(root/name)
        if response['params'] != expected[i] or entry['row_count'] != len(response['rows']):
            raise ValueError('Response request/count mismatch')
        rows = valid_rows(response['rows'], expected[i])
        selected_day, selected = select_rows(rows)
        if selected:
            if i != len(pages)-1: raise ValueError('Requests continued after latest candidate')
            chosen_day, chosen_rows = selected_day, selected
    if (not chosen_rows or manifest.get('schema_version') != 1 or manifest.get('source') != 'tushare.index_weight'
            or manifest.get('index_code') != INDEX or manifest['provider_snapshot_date'] != chosen_day
            or manifest['rows'] != chosen_rows or manifest['acquired_at'] != response['acquired_at']):
        raise ValueError('Acquisition manifest selection mismatch')
    stamp = datetime.fromisoformat(manifest['acquired_at'])
    if stamp.utcoffset() != timedelta(hours=8): raise ValueError('Acquisition timestamp timezone invalid')
    return manifest


def collect_constituent_evidence(client, requested_as_of, output, *, request_state,
                                  interval=.5, clock=time.time, sleep=time.sleep):
    requested = day(requested_as_of)
    if requested > datetime.fromtimestamp(clock(), TZ).date():
        raise ValueError('Requested membership date is in future')
    root = Path(output)
    if (root/'complete.json').exists():
        return verify_constituent_evidence(root, requested_as_of)
    if (root/'acquisition_manifest.json').exists():
        raise ValueError('Partial manifest retained; do not overwrite')
    root.mkdir(parents=True, exist_ok=True)
    migrate_request_state(request_state)
    binding = dict(schema_version=1, source='tushare.index_weight', index_code=INDEX, requested_as_of=requested_as_of)
    if (root/'binding.json').exists():
        if read(root/'binding.json') != binding: raise ValueError('Request binding mismatch')
    else: write(root/'binding.json', binding, exclusive=True)
    requests, pages = None, []
    for params in month_requests(requested_as_of):
        name = 'request_'+params['start_date'][:6]+'.json'
        path, proof = root/name, root/(name+'.sha.json')
        if path.exists() or proof.exists():
            if not path.is_file() or not proof.is_file() or read(proof) != dict(sha256=digest(path)):
                raise ValueError('Partial/tampered response shard retained')
            response = read(path)
            if response['params'] != params: raise ValueError('Shard request mismatch')
        else:
            if requests is None: requests = Requests(root, interval, clock, sleep, request_state)
            frame = requests.call(client.index_weight, params)
            if not isinstance(frame, pd.DataFrame) or set(frame.columns) != set(FIELDS) or frame.columns.duplicated().any():
                raise ValueError('SCHEMA: documented fields required')
            rows = valid_rows(frame[FIELDS].to_dict('records'), params)
            response = dict(params=params, rows=rows, acquired_at=datetime.fromtimestamp(clock(), TZ).isoformat())
            write(path, response, exclusive=True)
            write(proof, dict(sha256=digest(path)), exclusive=True)
        rows = valid_rows(response['rows'], params)
        pages.append(dict(path=name, params=params, sha256=digest(path), row_count=len(rows)))
        source_day, selected = select_rows(rows)
        if selected:
            manifest = dict(binding, pages=pages, provider_snapshot_date=source_day,
                            acquired_at=response['acquired_at'], rows=selected)
            write(root/'acquisition_manifest.json', manifest, exclusive=True)
            verify_constituent_evidence(root, requested_as_of, require_complete=False)
            write(root/'complete.json', dict(schema_version=1, manifest_sha256=digest(root/'acquisition_manifest.json')), exclusive=True)
            return verify_constituent_evidence(root, requested_as_of)
    raise ValueError('NO_SNAPSHOT: no source snapshot in three months')
