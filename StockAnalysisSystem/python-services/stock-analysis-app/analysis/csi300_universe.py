"""Immutable CSI300 identities. Consumer verifies source files without SDK imports."""
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import shutil

import pandas as pd


def identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))


def write_exclusive(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)


def iso_date(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('ISO date required')
    return datetime.strptime(value, '%Y-%m-%d').date()


def timestamp(value):
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.utcoffset() != timedelta(hours=8):
        raise ValueError('Shanghai timestamp required')
    return stamp


def _validate_rows(rows, params):
    seen = set()
    for r in rows:
        if (set(r) != {'index_code', 'con_code', 'trade_date', 'weight'} or r['index_code'] != '000300.SH'
                or not isinstance(r['con_code'], str) or not re.fullmatch(r'\d{6}\.(SH|SZ)', r['con_code'])
                or not isinstance(r['trade_date'], str) or not re.fullmatch(r'\d{8}', r['trade_date'])
                or not params['start_date'] <= r['trade_date'] <= params['end_date']):
            raise ValueError('Constituent source schema invalid')
        datetime.strptime(r['trade_date'], '%Y%m%d')
        if isinstance(r['weight'], bool) or not isinstance(r['weight'], (int, float)) or not math.isfinite(r['weight']) or r['weight'] < 0:
            raise ValueError('Original weight invalid')
        key = r['trade_date'], r['con_code']
        if key in seen: raise ValueError('Constituent duplicate')
        seen.add(key)


def read_acquisition(root):
    root = Path(root)
    manifest = read(root/'acquisition_manifest.json')
    if read(root/'complete.json') != dict(schema_version=1, manifest_sha256=digest(root/'acquisition_manifest.json')):
        raise ValueError('Source completion hash mismatch')
    binding = {k: manifest[k] for k in ('schema_version', 'source', 'index_code', 'requested_as_of')}
    if binding != dict(schema_version=1, source='tushare.index_weight', index_code='000300.SH',
                       requested_as_of=manifest['requested_as_of']) or read(root/'binding.json') != binding:
        raise ValueError('Source binding invalid')
    as_of = pd.Timestamp(iso_date(manifest['requested_as_of']))
    pages = manifest['pages']
    if not isinstance(pages, list) or not 1 <= len(pages) <= 3:
        raise ValueError('Source request coverage invalid')
    selected, source_date = [], None
    for i, entry in enumerate(pages):
        start = (as_of-pd.DateOffset(months=i)).replace(day=1)
        params = dict(index_code='000300.SH', start_date=start.strftime('%Y%m%d'),
                      end_date=min(start+pd.offsets.MonthEnd(0), as_of).strftime('%Y%m%d'))
        name = 'request_'+start.strftime('%Y%m')+'.json'
        if entry['path'] != name or entry['params'] != params or entry['sha256'] != digest(root/name):
            raise ValueError('Source response identity/hash invalid')
        if read(root/(name+'.sha.json')) != dict(sha256=digest(root/name)):
            raise ValueError('Source response proof invalid')
        response = read(root/name)
        if response['params'] != params or not isinstance(response['rows'], list) or entry['row_count'] != len(response['rows']):
            raise ValueError('Source request/count invalid')
        _validate_rows(response['rows'], params)
        timestamp(response['acquired_at'])
        if response['rows']:
            if i != len(pages)-1: raise ValueError('Source continued beyond latest available group')
            newest = max(r['trade_date'] for r in response['rows'])
            selected = [r for r in response['rows'] if r['trade_date'] == newest]
            source_date = datetime.strptime(newest, '%Y%m%d').date().isoformat()
    if (not selected or manifest['provider_snapshot_date'] != source_date
            or manifest['rows'] != selected or manifest['acquired_at'] != response['acquired_at']):
        raise ValueError('Source selection/time invalid')
    return dict(manifest, _source_manifest_sha256=digest(root/'acquisition_manifest.json'),
                _raw_response_sha256=pages[-1]['sha256'])


def build_universe(source_manifest, response_rows, frozen_at):
    source = source_manifest
    requested = iso_date(source['requested_as_of'])
    provider = iso_date(source['provider_snapshot_date'])
    acquired, frozen = timestamp(source['acquired_at']), timestamp(frozen_at)
    if (source.get('schema_version') != 1 or source.get('source') != 'tushare.index_weight'
            or source.get('index_code') != '000300.SH' or provider > requested
            or requested > acquired.date() or frozen < acquired or len(response_rows) != 300):
        raise ValueError('Universe source/count/time invalid')
    params = dict(start_date=provider.strftime('%Y%m%d'), end_date=provider.strftime('%Y%m%d'))
    _validate_rows(response_rows, params)
    if sorted(response_rows, key=lambda r:r['con_code']) != sorted(source['rows'], key=lambda r:r['con_code']):
        raise ValueError('Universe rows differ from selected source')
    codes = sorted(r['con_code'] for r in response_rows)
    if len(set(codes)) != 300: raise ValueError('Universe must have300 distinct identities')
    mapping = {code: i for i, code in enumerate(codes)}
    canonical_source = {k:v for k,v in source.items() if not k.startswith('_')}
    source_sha = source.get('_source_manifest_sha256', identity(canonical_source))
    raw_sha = source.get('_raw_response_sha256', identity(response_rows))
    version = 'csi300_'+identity(dict(index_code='000300.SH', provider_snapshot_date=str(provider),
        members=sorted(response_rows, key=lambda r:r['con_code']), source_sha256=source_sha))[:16]
    return dict(schema_version=1, pool_version=version, index_code='000300.SH', source='tushare.index_weight',
        provider_snapshot_date=str(provider), requested_as_of=str(requested), acquired_at=acquired.isoformat(),
        frozen_at=frozen.isoformat(), survivorship_bias=True, codes=codes, stockid2idx=mapping,
        codes_sha256=identity(codes), mapping_sha256=identity(mapping), source_manifest_sha256=source_sha,
        raw_response_sha256=raw_sha)


def publish_universe(acquisition_dir, output, frozen_at):
    source, output = Path(acquisition_dir).resolve(), Path(output).resolve()
    acquired = read_acquisition(source)
    manifest = build_universe(acquired, acquired['rows'], frozen_at)
    if (output.parent.name != 'csi300_fixed' or output.name != manifest['pool_version']
            or output.is_relative_to(source) or source.is_relative_to(output) or output.exists()):
        raise ValueError('New nonoverlapping fixed-universe directory required')
    output.mkdir(parents=True, exist_ok=False)
    target = output/'source'
    target.mkdir()
    source_files = ['binding.json', 'acquisition_manifest.json', 'complete.json']
    for page in read(source/'acquisition_manifest.json')['pages']:
        source_files.extend([page['path'], page['path']+'.sha.json'])
    for name in source_files:
        shutil.copyfile(source/name, target/name)
    write_exclusive(output/'manifest.json', manifest)
    seal = dict(schema_version=1, manifest_sha256=digest(output/'manifest.json'),
                source_files={name:digest(target/name) for name in source_files})
    write_exclusive(output/'seal.json', seal)
    write_exclusive(output/'complete.json', dict(schema_version=1, seal_sha256=digest(output/'seal.json')))
    return verify_universe(output)


def verify_universe(output):
    root = Path(output)
    try:
        seal, manifest = read(root/'seal.json'), read(root/'manifest.json')
        if read(root/'complete.json') != dict(schema_version=1, seal_sha256=digest(root/'seal.json')):
            raise ValueError('Pool completion proof invalid')
        if seal['manifest_sha256'] != digest(root/'manifest.json'):
            raise ValueError('Pool manifest hash invalid')
        source = read_acquisition(root/'source')
        files = ['binding.json', 'acquisition_manifest.json', 'complete.json']
        for page in source['pages']: files.extend([page['path'], page['path']+'.sha.json'])
        if seal['source_files'] != {name:digest(root/'source'/name) for name in files}:
            raise ValueError('Pool source files hash invalid')
        expected = build_universe(source, source['rows'], manifest['frozen_at'])
        if expected != manifest or root.name != manifest['pool_version']:
            raise ValueError('Pool identity/mapping mismatch')
        return manifest
    except (OSError, KeyError, TypeError) as exc:
        raise ValueError('Partial or invalid fixed-universe evidence') from exc
