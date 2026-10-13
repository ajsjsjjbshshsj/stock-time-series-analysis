"""Sealed missing-range responses with original-query cache provenance."""
from datetime import datetime
from pathlib import Path
import time
import pandas as pd

from app.market_data.forward_snapshot import read, write, digest
from app.market_data.repair_acquisition import validate_rows
from app.nightly.acquisition import scoped_path, rows_of, make_pacing, lazy_source_client, paced_call
from app.preparation.contracts import identity, iso, timestamp, TZ
from app.preparation.gap_plan import make_gap_request, validate_coverage
from app.nightly.contracts import is_a_share
from app.nightly.symbol_periods import eligible_on, period_for
from app.runtime_paths import resolve_reference,persist_reference


def validate_request(request, snapshot):
    if request != make_gap_request(request['method'], request['mode'], request['expected_codes'],
                                  request['expected_keys'], snapshot=snapshot):
        raise ValueError('GAP_REQUEST_BINDING_INVALID')


def _response(rows, request, snapshot):
    # Numeric/duplicate/capacity checks cover the entire original response;
    # the gap-specific scope policy below then audits bulk-only exclusions.
    validation = request
    if request['mode'] == 'full_date':
        validation = dict(request, expected_codes=sorted({row.get('ts_code') for row in rows
            if isinstance(row, dict) and type(row.get('ts_code')) is str}))
    validate_rows(rows, validation)
    target = {tuple(key) for key in request['expected_keys']}
    calendar = set(snapshot['sessions'])
    roster = {row['ts_code']: row for row in snapshot['members']}
    accepted, excluded = [], []
    for row in rows:
        compact = row['trade_date']
        day = f'{compact[:4]}-{compact[4:6]}-{compact[6:]}'
        code = row['ts_code']
        if day not in calendar:
            raise ValueError('GAP_RESPONSE_CALENDAR_SCOPE_INVALID')
        if request['mode'] == 'stock_range' and (code, day) not in target:
            raise ValueError('GAP_RESPONSE_SCOPE_INVALID')
        if request['mode'] == 'full_date' and (code, day) not in target:
            # Full-market wire responses may include non-A or explicit expired
            # symbols. Retain them in the raw proof, never relabel/import them.
            period = period_for(code)
            invalid_period = (period['valid_from'] is not None and day < period['valid_from']
                              or period['valid_until'] is not None and day >= period['valid_until'])
            member = roster.get(code)
            if not is_a_share(code) or invalid_period or (member is not None and eligible_on(
                    code, day, list_date=member['list_date'], delist_date=member['delist_date'])):
                excluded.append(dict(ts_code=code, trade_date=day, reason='OUTSIDE_TARGET_KEYS'))
                continue
            raise ValueError('GAP_RESPONSE_SCOPE_INVALID')
        accepted.append(row)
    return accepted, excluded


def _paths(directory, request):
    return tuple(scoped_path(directory, f"shards/{request['id']}{suffix}")
                 for suffix in ('.json', '.parquet', '.proof.json'))


def _manifest(root):
    shards = scoped_path(root, 'shards')
    files = {}
    for item in sorted(shards.iterdir()):
        path = scoped_path(root, 'shards/' + item.name)
        if path.is_file():
            files[item.name] = digest(path)
    return dict(binding_sha256=digest(scoped_path(root, 'binding.json')), files=files)


def _verify_completion(root):
    manifest = scoped_path(root, 'manifest.json')
    complete = scoped_path(root, 'complete.json')
    if (not complete.is_file() or not manifest.is_file()
            or read(manifest) != _manifest(root)
            or read(complete) != dict(manifest_sha256=digest(manifest))):
        raise ValueError('GAP_COMPLETION_HASH_INVALID')


def verify_gap_shard(directory, request, *, snapshot,paths=None):
    validate_request(request, snapshot)
    root = Path(directory).resolve()
    if paths is not None:
        persist_reference(root, paths)  # Reject an escaped source before any read.
    path, raw, proof = _paths(root, request)
    if read(proof) != dict(sha256=digest(path), raw_sha256=digest(raw)):
        raise ValueError('GAP_SHARD_HASH_INVALID')
    body = read(path)
    if (set(body) != {'request', 'acquired_at', 'rows', 'row_count', 'parent'}
            or body['request'] != request or type(body['row_count']) is not int
            or body['row_count'] != len(body['rows'])):
        raise ValueError('GAP_SHARD_BINDING_INVALID')
    stamp = timestamp(body['acquired_at'])
    if stamp.isoformat() != body['acquired_at'] or stamp.date() < iso(request['end']):
        raise ValueError('GAP_SOURCE_TIME_INVALID')
    frame = pd.read_parquet(raw)
    if (frame.columns.duplicated().any() or list(frame.columns) != request['fields']
            or rows_of(frame) != body['rows']):
        raise ValueError('GAP_RAW_PROOF_INVALID')
    accepted, exclusions = _response(body['rows'], request, snapshot)
    parent = body['parent']
    if parent is not None:
        original = _verify_pointer(parent,paths=paths)
        target = {tuple(key) for key in request['expected_keys']}
        extracted = [row for row in original['raw_rows'] if (row['ts_code'],
            f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:]}") in target]
        if extracted != body['rows']:
            raise ValueError('GAP_PARENT_EXTRACTION_INVALID')
    return dict(body, rows=accepted, raw_rows=body['rows'], exclusions=exclusions, source_directory=str(root),
        raw_sha256=digest(raw), manifest_sha256=digest(path), request_complete=True,
        data_ready=len(accepted) == len(request['expected_keys']))


def _verify_pointer(pointer,*,paths=None):
    root = resolve_reference(pointer['source_directory'],paths)
    _verify_completion(root)
    binding_path = scoped_path(root, 'binding.json')
    binding = read(binding_path)
    if (digest(binding_path) != pointer['binding_sha256']
            or pointer['request'] not in binding['requests']):
        raise ValueError('GAP_PARENT_BINDING_HASH_INVALID')
    snapshot = read(resolve_reference(binding['snapshot_ref']['path'],paths))
    if identity(snapshot) != binding['snapshot_ref']['sha256']:
        raise ValueError('GAP_PARENT_SNAPSHOT_HASH_INVALID')
    result = verify_gap_shard(root, pointer['request'], snapshot=snapshot,paths=paths)
    if (result['raw_sha256'] != pointer['raw_sha256']
            or result['manifest_sha256'] != pointer['manifest_sha256']):
        raise ValueError('GAP_PARENT_HASH_INVALID')
    return result


def _index_root(source_store):
    return scoped_path(source_store.root, 'gap_source_index')


def lookup_cached_gap(request, *, snapshot, source_store,paths=None):
    validate_request(request, snapshot)
    root = _index_root(source_store)
    locations = [scoped_path(root, f"{request['method']}/stocks/{code}") for code in request['expected_codes']]
    locations.extend(scoped_path(root, f"{request['method']}/dates/{day}") for day in sorted({key[1] for key in request['expected_keys']}))
    index_files = sorted({path for location in locations for path in location.glob('*.json')})
    target = {tuple(key) for key in request['expected_keys']}
    for path in index_files:
        path = scoped_path(root, path.relative_to(root).as_posix())
        pointer = read(path)
        old = pointer['request']
        if old['method'] != request['method'] or not old['start'] <= request['start'] <= request['end'] <= old['end']:
            continue
        # A proven query covers keys even if the provider returned no quote.
        if old['mode']=='full_date':
            parent_root=resolve_reference(pointer['source_directory'],paths)
            _verify_completion(parent_root)
            parent_binding=read(scoped_path(parent_root,'binding.json'))
            parent_snapshot=read(resolve_reference(parent_binding['snapshot_ref']['path'],paths))
            if identity(parent_snapshot)!=parent_binding['snapshot_ref']['sha256']:
                raise ValueError('GAP_PARENT_SNAPSHOT_HASH_INVALID')
            from app.preparation.gap_plan import eligible_bits
            valid=eligible_bits(parent_snapshot,codes=request['expected_codes'])
            positions={day:i for i,day in enumerate(parent_snapshot['sessions'])}
            if any(code not in valid or day not in positions or not valid[code] & (1 << positions[day]) for code,day in target):
                continue
        elif not target <= {tuple(key) for key in old['expected_keys']}:
            continue
        original = _verify_pointer(pointer,paths=paths)
        rows = [row for row in original['raw_rows'] if (row['ts_code'],
            f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:]}") in target]
        _response(rows, request, snapshot)
        return dict(parent=pointer, rows=rows, acquired_at=original['acquired_at'])
    return None


def _register(source_store, directory, request, result,*,paths=None):
    # Derivations do not become parents, keeping provenance depth bounded at one.
    if result['parent'] is not None:
        return
    pointer = dict(source_directory=persist_reference(directory,paths), request=request,
        binding_sha256=digest(scoped_path(directory,'binding.json')), raw_sha256=result['raw_sha256'],
        manifest_sha256=result['manifest_sha256'])
    root = scoped_path(_index_root(source_store), request['method'])
    locations = ([scoped_path(root,'dates/'+request['start'])] if request['mode'] == 'full_date'
                 else [scoped_path(root,'stocks/'+code) for code in request['expected_codes']])
    for location in locations:
        location.mkdir(parents=True, exist_ok=True)
        path = scoped_path(location,identity(pointer)+'.json')
        if path.exists():
            if read(path) != pointer:
                raise ValueError('GAP_SOURCE_INDEX_CHANGED')
        else:
            write(path, pointer, exclusive=True)


class GapAcquirer:
    def __init__(self, client_factory, requests_factory, *, source_store, clock=time.time,paths=None):
        self.client_factory, self.requests_factory = client_factory, requests_factory
        self.source_store, self.clock = source_store, clock
        self.paths=paths

    def collect(self, requests, directory, *, snapshot, on_progress=None):
        validate_coverage(snapshot)
        for request in requests:
            validate_request(request, snapshot)
        if len({r['id'] for r in requests}) != len(requests):
            raise ValueError('GAP_PLAN_DUPLICATE_REQUEST')
        if not requests:
            return dict(complete=True, shards=[], api_requests=0, cache_hits=0, empty_requests=0)
        root = Path(directory).resolve()
        if self.paths is not None:
            persist_reference(root,self.paths)  # Before mkdir, including junctions.
        binding_path=scoped_path(root,'binding.json')
        manifest_path=scoped_path(root,'manifest.json')
        complete_path=scoped_path(root,'complete.json')
        shard_root=scoped_path(root,'shards')
        if complete_path.exists():
            _verify_completion(root)
        root.mkdir(parents=True, exist_ok=True)
        shard_root.mkdir(exist_ok=True)
        snapshot_root = scoped_path(self.source_store.root,'gap_snapshots')
        snapshot_root.mkdir(parents=True, exist_ok=True)
        snapshot_path = scoped_path(snapshot_root,snapshot['coverage_sha256']+'.json')
        if snapshot_path.exists():
            if read(snapshot_path) != snapshot:
                raise ValueError('GAP_SHARED_SNAPSHOT_CHANGED')
        else:
            write(snapshot_path, snapshot, exclusive=True)
        binding = dict(schema_version=1, requests=requests,
            snapshot_ref=dict(path=persist_reference(snapshot_path,self.paths), sha256=identity(snapshot)))
        if binding_path.exists():
            old=read(binding_path)
            equivalent=old==binding
            if not equivalent and self.paths is not None and set(old)==set(binding) and isinstance(old.get('snapshot_ref'),dict) and set(old['snapshot_ref'])=={'path','sha256'}:
                # Compare a temporary resolved view; never reseal the original
                # binding or any parent proof merely because its root moved.
                normalized=dict(old,snapshot_ref=dict(old['snapshot_ref'],path=persist_reference(resolve_reference(old['snapshot_ref']['path'],self.paths),self.paths)))
                equivalent=normalized==binding
            if not equivalent:
                raise ValueError('GAP_PLAN_CHANGED')
        else:
            write(binding_path, binding, exclusive=True)
        shards, api, hits, empty = [], 0, 0, 0
        pacing = client = None
        for index, request in enumerate(requests):
            if on_progress is not None:
                on_progress(index, len(requests))
            path, raw, proof = _paths(root, request)
            if path.exists() or proof.exists():
                result = verify_gap_shard(root, request, snapshot=snapshot,paths=self.paths)
                hits += 1
            else:
                if raw.exists() or complete_path.exists():
                    raise ValueError('GAP_UNVERIFIED_RESPONSE_RETAINED')
                cached = lookup_cached_gap(request, snapshot=snapshot, source_store=self.source_store,paths=self.paths)
                if cached is not None:
                    frame = pd.DataFrame(cached['rows'], columns=request['fields'])
                    parent, acquired = cached['parent'], cached['acquired_at']
                    hits += 1
                else:
                    if pacing is None:
                        pacing = make_pacing(self.requests_factory, root/'pacing')
                    if client is None:
                        client = lazy_source_client(self.client_factory)
                    frame = paced_call(pacing, getattr(client, request['method']), request['params'], self.clock)
                    parent, acquired = None, datetime.fromtimestamp(self.clock(), TZ).isoformat()
                    api += 1
                if (not isinstance(frame, pd.DataFrame) or frame.columns.duplicated().any()
                        or set(frame.columns) != set(request['fields'])):
                    raise ValueError('GAP_RESPONSE_FIELDS_INVALID')
                frame = frame[request['fields']].copy()
                frame.to_parquet(raw, index=False)
                rows = rows_of(frame)
                _response(rows, request, snapshot)
                write(path, dict(request=request, acquired_at=acquired, rows=rows,
                    row_count=len(rows), parent=parent), exclusive=True)
                write(proof, dict(sha256=digest(path), raw_sha256=digest(raw)), exclusive=True)
                result = verify_gap_shard(root, request, snapshot=snapshot,paths=self.paths)
            shards.append(result)
            empty += int(not result['rows'])
        manifest = _manifest(root)
        if complete_path.exists():
            _verify_completion(root)
        else:
            write(manifest_path, manifest, exclusive=True)
            write(complete_path, dict(manifest_sha256=digest(manifest_path)), exclusive=True)
        for request,result in zip(requests,shards):
            _register(self.source_store, root, request, result,paths=self.paths)
        if on_progress is not None:
            on_progress(len(requests), len(requests))
        return dict(complete=True, shards=shards, api_requests=api, cache_hits=hits, empty_requests=empty)
