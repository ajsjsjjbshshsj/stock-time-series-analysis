"""Sealed missing-range responses with original-query cache provenance."""
from datetime import datetime
from pathlib import Path
import re
import time
import pandas as pd

from app.market_data.forward_snapshot import read, write, digest
from app.market_data.repair_acquisition import validate_rows
from app.nightly.acquisition import scoped_path, rows_of, make_pacing, lazy_source_client, paced_call
from app.preparation.contracts import identity, iso, timestamp, TZ
from app.preparation.gap_plan import make_gap_request, validate_coverage
from app.nightly.contracts import is_a_share
from app.nightly.symbol_periods import eligible_on, period_for


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
    path = scoped_path(directory, f"shards/{request['id']}.json")
    return path, path.with_suffix('.parquet'), path.with_suffix('.proof.json')


def verify_gap_shard(directory, request, *, snapshot):
    validate_request(request, snapshot)
    root = Path(directory).resolve()
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
        original = _verify_pointer(parent)
        target = {tuple(key) for key in request['expected_keys']}
        extracted = [row for row in original['rows'] if (row['ts_code'],
            f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:]}") in target]
        if extracted != body['rows']:
            raise ValueError('GAP_PARENT_EXTRACTION_INVALID')
    return dict(body, rows=accepted, exclusions=exclusions, source_directory=str(root),
        raw_sha256=digest(raw), manifest_sha256=digest(path), request_complete=True,
        data_ready=len(accepted) == len(request['expected_keys']))


def _verify_pointer(pointer):
    root = Path(pointer['source_directory']).resolve()
    binding = read(root/'binding.json')
    if (digest(root/'binding.json') != pointer['binding_sha256']
            or pointer['request'] not in binding['requests']):
        raise ValueError('GAP_PARENT_BINDING_HASH_INVALID')
    result = verify_gap_shard(root, pointer['request'], snapshot=binding['snapshot'])
    if (result['raw_sha256'] != pointer['raw_sha256']
            or result['manifest_sha256'] != pointer['manifest_sha256']):
        raise ValueError('GAP_PARENT_HASH_INVALID')
    return result


def _index_root(source_store):
    return Path(source_store.root).resolve()/'gap_source_index'


def lookup_cached_gap(request, *, snapshot, source_store):
    validate_request(request, snapshot)
    root = _index_root(source_store)
    locations = [root/request['method']/'stocks'/code for code in request['expected_codes']]
    locations.extend(root/request['method']/'dates'/day for day in sorted({key[1] for key in request['expected_keys']}))
    paths = sorted({path for location in locations for path in location.glob('*.json')})
    target = {tuple(key) for key in request['expected_keys']}
    for path in paths:
        pointer = read(path)
        old = pointer['request']
        if old['method'] != request['method'] or not old['start'] <= request['start'] <= request['end'] <= old['end']:
            continue
        # A proven query covers keys even if the provider returned no quote.
        if not target <= {tuple(key) for key in old['expected_keys']}:
            continue
        original = _verify_pointer(pointer)
        rows = [row for row in original['rows'] if (row['ts_code'],
            f"{row['trade_date'][:4]}-{row['trade_date'][4:6]}-{row['trade_date'][6:]}") in target]
        _response(rows, request, snapshot)
        return dict(parent=pointer, rows=rows, acquired_at=original['acquired_at'])
    return None


def _register(source_store, directory, request, result):
    # Derivations do not become parents, keeping provenance depth bounded at one.
    if result['parent'] is not None:
        return
    pointer = dict(source_directory=str(Path(directory).resolve()), request=request,
        binding_sha256=digest(Path(directory)/'binding.json'), raw_sha256=result['raw_sha256'],
        manifest_sha256=result['manifest_sha256'])
    root = _index_root(source_store)/request['method']
    locations = ([root/'dates'/request['start']] if request['mode'] == 'full_date'
                 else [root/'stocks'/code for code in request['expected_codes']])
    for location in locations:
        location.mkdir(parents=True, exist_ok=True)
        path = location/(identity(pointer)+'.json')
        if path.exists():
            if read(path) != pointer:
                raise ValueError('GAP_SOURCE_INDEX_CHANGED')
        else:
            write(path, pointer, exclusive=True)


class GapAcquirer:
    def __init__(self, client_factory, requests_factory, *, source_store, clock=time.time):
        self.client_factory, self.requests_factory = client_factory, requests_factory
        self.source_store, self.clock = source_store, clock

    def collect(self, requests, directory, *, snapshot, on_progress=None):
        validate_coverage(snapshot)
        for request in requests:
            validate_request(request, snapshot)
        if len({r['id'] for r in requests}) != len(requests):
            raise ValueError('GAP_PLAN_DUPLICATE_REQUEST')
        if not requests:
            return dict(complete=True, shards=[], api_requests=0, cache_hits=0, empty_requests=0)
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        (root/'shards').mkdir(exist_ok=True)
        binding = dict(schema_version=1, requests=requests, snapshot=snapshot)
        if (root/'binding.json').exists():
            if read(root/'binding.json') != binding:
                raise ValueError('GAP_PLAN_CHANGED')
        else:
            write(root/'binding.json', binding, exclusive=True)
        shards, api, hits, empty = [], 0, 0, 0
        pacing = client = None
        for index, request in enumerate(requests):
            if on_progress is not None:
                on_progress(index, len(requests))
            path, raw, proof = _paths(root, request)
            if path.exists() or proof.exists():
                result = verify_gap_shard(root, request, snapshot=snapshot)
                hits += 1
            else:
                if raw.exists() or (root/'complete.json').exists():
                    raise ValueError('GAP_UNVERIFIED_RESPONSE_RETAINED')
                cached = lookup_cached_gap(request, snapshot=snapshot, source_store=self.source_store)
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
                result = verify_gap_shard(root, request, snapshot=snapshot)
            _register(self.source_store, root, request, result)
            shards.append(result)
            empty += int(not result['rows'])
        manifest = dict(binding_sha256=digest(root/'binding.json'),
            files={path.name: digest(path) for path in sorted((root/'shards').iterdir()) if path.is_file()})
        if (root/'complete.json').exists():
            if read(root/'manifest.json') != manifest or read(root/'complete.json') != dict(manifest_sha256=digest(root/'manifest.json')):
                raise ValueError('GAP_COMPLETION_HASH_INVALID')
        else:
            write(root/'manifest.json', manifest, exclusive=True)
            write(root/'complete.json', dict(manifest_sha256=digest(root/'manifest.json')), exclusive=True)
        if on_progress is not None:
            on_progress(len(requests), len(requests))
        return dict(complete=True, shards=shards, api_requests=api, cache_hits=hits, empty_requests=empty)
