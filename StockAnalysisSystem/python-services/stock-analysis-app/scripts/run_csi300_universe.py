"""One-shot CSI300 membership freeze and read-only data readiness, no training."""
import argparse
from datetime import datetime, time
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid
import pandas as pd

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from analysis.csi300_universe import (read, digest, identity, write_exclusive, iso_date,
    read_acquisition, build_universe, publish_universe, verify_universe)
from analysis.csi300_readiness import diagnose_readiness, frame_identity, normalize
from analysis.strategy_dates import SHANGHAI, validate_session_calendar
from data_loader.csi300_readiness_source import read_local_sources, factor_source


def now(): return datetime.now(SHANGHAI)


def inherit_request_state():
    """Path only: atomic inheritance belongs to the Collector request boundary."""
    return APP.parents[1]/'.runtime/market_requests/request_state.json'


def collect(output, requested):
    collector = APP.parent/'python-collector'
    state = inherit_request_state()
    result = subprocess.run([sys.executable, str(collector/'scripts/collect_csi300_constituents.py'),
        '--as-of', requested, '--output', str(output), '--request-state', str(state)],
        cwd=collector, capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    if result.returncode:
        code = 'ACQUISITION_FAILED'
        if (output/'acquisition.json').exists():
            candidate = read(output/'acquisition.json').get('failure')
            if candidate in ('PERMISSION','NETWORK','MINUTE_QUOTA','HOURLY_QUOTA','QUOTA_COOLDOWN','PROVIDER'):
                code = candidate
        raise ValueError(code+': safe evidence at acquisition directory')


def run_freeze(root, requested_as_of=None, *, clock=now, collect=collect):
    root = Path(root).resolve()
    if root != (APP/'models/universes/csi300_fixed').resolve():
        raise ValueError('Canonical fixed-pool root required')
    active = root/'active_pool.json'
    if active.exists():
        binding = read(active)
        version = binding.get('pool_version', '')
        if not isinstance(version,str) or not version.startswith('csi300_') or len(version)!=23 or any(c not in '0123456789abcdef' for c in version[7:]):
            raise ValueError('Active pool identity invalid')
        manifest = verify_universe(root/version)
        if binding != dict(pool_version=version, manifest_sha256=digest(root/version/'manifest.json')):
            raise ValueError('Active pool hash mismatch')
        if requested_as_of is not None and requested_as_of != manifest['requested_as_of']:
            raise ValueError('Fixed pool cannot be silently replaced by a different as-of date')
        return manifest
    stamp = clock().astimezone(SHANGHAI)
    requested = requested_as_of or str(stamp.date())
    if iso_date(requested) > stamp.date(): raise ValueError('Future membership request rejected')
    source = root/'.acquisitions'/requested.replace('-','')
    root.mkdir(parents=True, exist_ok=True)
    if not (source/'complete.json').exists(): collect(source, requested)
    acquired = read_acquisition(source)
    if acquired['requested_as_of'] != requested: raise ValueError('Acquisition request mismatch')
    manifest = build_universe(acquired, acquired['rows'], clock().astimezone(SHANGHAI))
    output = root/manifest['pool_version']
    if output.exists():
        manifest = verify_universe(output)
    else:
        manifest = publish_universe(source, output, datetime.fromisoformat(manifest['frozen_at']))
    write_exclusive(active, dict(pool_version=manifest['pool_version'], manifest_sha256=digest(output/'manifest.json')))
    return manifest


def _copy_pool(source, target):
    manifest = verify_universe(source)
    dest = target/'pool'/manifest['pool_version']
    (dest/'source').mkdir(parents=True)
    for name in ('manifest.json','seal.json','complete.json'): shutil.copyfile(source/name, dest/name)
    for name in read(source/'seal.json')['source_files']: shutil.copyfile(source/'source'/name, dest/'source'/name)
    return dest


def run_diagnose(pool, calendar, selected_stock, output, *, clock=now, read_sources=read_local_sources,
                  start=None, end=None, factor_manifest=None):
    pool, output = Path(pool).resolve(), Path(output).resolve()
    universe = verify_universe(pool)
    if selected_stock not in universe['codes']: raise ValueError('Selected stock outside fixed pool')
    if output.parent != (APP/'models/universes/csi300_readiness').resolve() or output.exists() or output.is_relative_to(pool):
        raise ValueError('New canonical readiness directory required')
    stamp = clock().astimezone(SHANGHAI)
    opened = None
    try:
        opened = validate_session_calendar(calendar)
        if not calendar['start'] <= str(stamp.date()) <= calendar['end']: opened = None
    except (ValueError, KeyError, TypeError): pass
    if start is not None: iso_date(start)
    if end is not None:
        iso_date(end)
        if datetime.combine(iso_date(end), time(16), SHANGHAI) >= stamp:
            raise ValueError('Future/intraday diagnostic end rejected')
    if opened:
        completed = [d for d in opened if datetime.combine(iso_date(d), time(16), SHANGHAI) < stamp]
        target = end or (completed[-1] if completed else None)
        if target is None or target not in completed: raise ValueError('No completed requested session')
        requested_start = start or str((pd.Timestamp(target)-pd.DateOffset(years=3)).date())
        if requested_start > target: raise ValueError('Diagnostic range reversed')
        if factor_manifest is None:
            daily, basic, factors, source_identity = read_sources(universe, requested_start, target)
        else:
            daily, basic, factors, source_identity = read_sources(universe, requested_start, target, factor_manifest=factor_manifest)
        for key, frame in [('daily',daily),('basic',basic),('factors',factors)]:
            if source_identity.get(key+'_sha256') != frame_identity(frame):
                raise ValueError('Source frame identity mismatch')
    else:
        target = None
        requested_start = start or str((pd.Timestamp(stamp.date())-pd.DateOffset(years=3)).date())
        daily, basic, factors = pd.DataFrame(), pd.DataFrame(), None
        source_identity = dict(source_mode='no_calendar', queries=0, factor_verified=False,
                              daily_sha256=frame_identity(daily), basic_sha256=frame_identity(basic), factors_sha256=None)
        calendar = None
    daily, basic = normalize(daily), normalize(basic, 'list_date')
    context = dict(pool_version=universe['pool_version'], selected_stock=selected_stock,
                    request_time=stamp.isoformat(), start_date=requested_start, end_date=target, calendar=calendar)
    report = diagnose_readiness(universe,daily,basic,calendar,factors=factors,
        factor_verified=source_identity.get('factor_verified',False),start_date=requested_start,end_date=target,
        selected_stock=selected_stock,request_time=stamp,source_identity=source_identity)
    output.mkdir(parents=True, exist_ok=False)
    _copy_pool(pool,output)
    snapshots = output/'source'
    snapshots.mkdir()
    daily.to_parquet(snapshots/'daily.parquet',index=False)
    basic.to_parquet(snapshots/'basic.parquet',index=False)
    if factors is not None: factors.to_parquet(snapshots/'factors.parquet',index=False)
    if source_identity.get('factor_verified'):
        evidence = source_identity.get('factor_evidence')
        if not evidence: raise ValueError('Verified factor source evidence missing')
        shutil.copyfile(evidence['manifest_path'], snapshots/'factor_manifest.json')
        shutil.copyfile(evidence['data_path'], snapshots/'factor_original.parquet')
    write_exclusive(output/'context.json',context)
    write_exclusive(output/'source_identity.json',source_identity)
    write_exclusive(output/'readiness.json',report)
    files = {str(p.relative_to(output)).replace('\\','/'):digest(p) for p in output.rglob('*') if p.is_file()}
    write_exclusive(output/'seal.json', dict(schema_version=1,files=files))
    verify_readiness(output, require_complete=False)
    write_exclusive(output/'complete.json',dict(schema_version=1,seal_sha256=digest(output/'seal.json')))
    return verify_readiness(output)


def verify_readiness(output, *, require_complete=True):
    root = Path(output)
    try:
        seal = read(root/'seal.json')
        if require_complete and read(root/'complete.json') != dict(schema_version=1,seal_sha256=digest(root/'seal.json')):
            raise ValueError('Readiness completion hash invalid')
        actual = {str(p.relative_to(root)).replace('\\','/'):digest(p) for p in root.rglob('*')
                  if p.is_file() and p not in (root/'seal.json',root/'complete.json')}
        if seal.get('schema_version') != 1 or actual != seal['files']: raise ValueError('Readiness source/report hashes invalid')
        context, source = read(root/'context.json'), read(root/'source_identity.json')
        version = context['pool_version']
        if not isinstance(version,str) or '/' in version or '\\' in version: raise ValueError('Pool path invalid')
        pool = verify_universe(root/'pool'/version)
        daily = normalize(pd.read_parquet(root/'source/daily.parquet'))
        basic = normalize(pd.read_parquet(root/'source/basic.parquet'),'list_date')
        factors = normalize(pd.read_parquet(root/'source/factors.parquet')) if (root/'source/factors.parquet').is_file() else None
        for key, frame in [('daily',daily),('basic',basic),('factors',factors)]:
            if source.get(key+'_sha256') != frame_identity(frame): raise ValueError('Frozen frame hash mismatch')
        if source.get('factor_verified'):
            original, actual_evidence = factor_source(root/'source/factor_manifest.json',
                        data_path_override=root/'source/factor_original.parquet')
            recorded = source.get('factor_evidence', {})
            if any(recorded.get(k) != actual_evidence[k] for k in ('manifest_sha256','data_sha256')):
                raise ValueError('Factor evidence changed after source read')
            expected = original[original.ts_code.isin(pool['codes']) & original.trade_date.between(context['start_date'],context['end_date'])]
            if frame_identity(expected) != frame_identity(factors): raise ValueError('Factor subset differs from frozen source')
        expected = diagnose_readiness(pool,daily,basic,context['calendar'],factors=factors,
            factor_verified=source.get('factor_verified',False),start_date=context['start_date'],end_date=context['end_date'],
            selected_stock=context['selected_stock'],request_time=datetime.fromisoformat(context['request_time']),source_identity=source)
        if expected != read(root/'readiness.json'): raise ValueError('Readiness conclusion is not reproducible')
        return expected
    except (OSError,KeyError,TypeError) as error:
        raise ValueError('Partial/invalid readiness evidence') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['freeze','diagnose','verify'],required=True)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--pool',type=Path)
    parser.add_argument('--as-of')
    parser.add_argument('--calendar',type=Path)
    parser.add_argument('--stock')
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--factor-manifest',type=Path)
    args = parser.parse_args(argv)
    try:
        if args.stage == 'freeze':
            root = args.output or APP/'models/universes/csi300_fixed'
            result = run_freeze(root,args.as_of)
            print('CSI300_POOL_COMPLETE version='+result['pool_version']+' stocks=300 source_date='+result['provider_snapshot_date'])
        elif args.stage == 'diagnose':
            if args.pool is None or args.stock is None: raise ValueError('Pool and selected stock required')
            calendar = read(args.calendar) if args.calendar and args.calendar.is_file() else None
            output = args.output or APP/'models/universes/csi300_readiness'/uuid.uuid4().hex
            result = run_diagnose(args.pool,calendar,args.stock,output,start=args.start,end=args.end,factor_manifest=args.factor_manifest)
            print('CSI300_READINESS '+result['status']+' report='+str(output/'readiness.json'))
            print(json.dumps({k:dict(data_ready=v['data_ready'],model_ready=v['model_ready'],reasons=v['reasons']) for k,v in result['strategies'].items()},ensure_ascii=False))
        else:
            if args.output is None: raise ValueError('Explicit completed output required')
            result = verify_readiness(args.output) if (args.output/'readiness.json').exists() else verify_universe(args.output)
            print('CSI300_VERIFIED '+result.get('status',result['pool_version']))
        return 0
    except Exception as error:
        code = 'INVALID_INPUT'
        for candidate in ('PERMISSION','NETWORK','MINUTE_QUOTA','HOURLY_QUOTA','QUOTA_COOLDOWN','SOURCE_UNAVAILABLE','ACQUISITION_FAILED'):
            if str(error).startswith(candidate): code=candidate
        print('CSI300_FAILED stage='+args.stage+' code='+code+'; preserve source evidence',file=sys.stderr)
        return 1


if __name__=='__main__': sys.exit(main())
