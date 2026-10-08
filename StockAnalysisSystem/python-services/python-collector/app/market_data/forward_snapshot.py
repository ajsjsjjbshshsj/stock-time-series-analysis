"""Serial, bounded forward acquisitions. Never touch the database or frozen history."""
from datetime import datetime, timedelta, timezone, date
import hashlib
import json
from pathlib import Path
import re
import time

import numpy as np
import pandas as pd

DAILY = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount']
FACTOR = ['ts_code', 'trade_date', 'adj_factor']
TZ = timezone(timedelta(hours=8))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, payload, exclusive=False):
    with Path(path).open('x' if exclusive else 'w', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, allow_nan=False, indent=2)


def day(value):
    if type(value) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Expected ISO date')
    return date.fromisoformat(value)


class Requests:
    """Persist pacing and quota evidence without preserving upstream exception text."""
    def __init__(self, directory, interval, clock, sleep):
        self.path = Path(directory)/'acquisition.json'
        self.interval, self.clock, self.sleep = max(.5, float(interval)), clock, sleep
        if not np.isfinite(self.interval):
            raise ValueError('Finite request interval required')
        self.state = read(self.path) if self.path.exists() else {}
        if self.state.get('retry_not_before', 0) > clock():
            raise RuntimeError('QUOTA_COOLDOWN: retry deadline not reached')

    def call(self, method, params):
        for attempt in range(3):
            previous = self.state.get('last_request_at')
            if previous is not None:
                self.sleep(max(0., self.interval-(self.clock()-previous)))
            self.state['last_request_at'] = self.clock()
            self.state['request'] = params
            try:
                result = method(**params)
            except Exception as error:
                message = str(error).lower()
                permission = any(x in message for x in ('token', '权限', '积分', 'unauthorized', 'forbidden', '参数'))
                hour = any(x in message for x in ('小时', 'hour'))
                minute = any(x in message for x in ('分钟', '频率', 'rate limit', '429', 'too many requests'))
                network = any(x in message for x in ('timeout', 'timed out', 'connection', '502', '503', '504', 'temporary'))
                kind = 'PERMISSION' if permission else 'HOURLY_QUOTA' if hour else 'MINUTE_QUOTA' if minute else 'NETWORK' if network else 'PROVIDER'
                self.state.update(failure=kind, request=params, attempts=attempt+1)
                if kind in ('HOURLY_QUOTA', 'MINUTE_QUOTA'):
                    self.state['retry_not_before'] = self.clock()+(3601 if hour else 61)
                write(self.path, self.state)
                if kind != 'NETWORK' or attempt == 2:
                    raise RuntimeError(f'ACQUISITION_{kind}: evidence retained') from None
                self.sleep(2**(attempt+1))
            else:
                self.state.pop('failure', None)
                self.state.pop('retry_not_before', None)
                write(self.path, self.state)
                return result


def _market(frame, code, dates, kind):
    columns = DAILY if kind == 'daily' else FACTOR
    if frame is None or frame.empty or frame.columns.duplicated().any() or not set(columns).issubset(frame):
        raise ValueError('Incomplete market response schema')
    result = frame[columns].copy()
    result['trade_date'] = pd.to_datetime(result.trade_date.astype(str), errors='raise').dt.strftime('%Y-%m-%d')
    if (result[['ts_code','trade_date']].isna().any().any() or set(result.ts_code) != {code}
            or set(result.trade_date) != set(dates) or result.duplicated(['ts_code','trade_date']).any()):
        raise ValueError('Market response date/universe coverage mismatch')
    for name in columns[2:]:
        result[name] = pd.to_numeric(result[name], errors='raise').astype(float)
    values = result[columns[2:]].to_numpy()
    if not np.isfinite(values).all() or (values[:, :4 if kind == 'daily' else 1] <= 0).any():
        raise ValueError('Invalid market price/factor values')
    if kind == 'daily' and (values[:, 4:] < 0).any():
        raise ValueError('Invalid market volume/amount')
    return result.sort_values('trade_date').reset_index(drop=True)


def _request_identity(codes, dates, binding):
    if (not codes or codes != sorted(set(codes)) or any(not re.fullmatch(r'\d{6}\.SZ', c) for c in codes)
            or not dates or dates != sorted(set(dates))):
        raise ValueError('Invalid acquisition universe/dates')
    for value in dates:
        day(value)
    json.dumps(binding, allow_nan=False)
    return dict(codes=codes, dates=dates, binding=binding)


def load_snapshot(directory, binding):
    directory = Path(directory)
    manifest = read(directory/'acquisition_manifest.json')
    if (manifest['binding'] != binding or manifest.get('volume_unit') != 'lots'
            or manifest.get('amount_unit') != 'thousand_CNY'
            or digest(directory/'daily.parquet') != manifest['daily_sha256']
            or digest(directory/'factors.parquet') != manifest['factor_sha256']):
        raise ValueError('Acquisition identity/units/hash mismatch')
    _request_identity(manifest['codes'], manifest['dates'], binding)
    frames = []
    for name in ('daily', 'factor'):
        filename = 'daily.parquet' if name == 'daily' else 'factors.parquet'
        frame = pd.read_parquet(directory/filename)
        if set(frame.ts_code) != set(manifest['codes']):
            raise ValueError('Snapshot universe mismatch')
        parts = [_market(frame[frame.ts_code == code], code, manifest['dates'], name) for code in manifest['codes']]
        combined = pd.concat(parts, ignore_index=True)
        if len(combined) != manifest[name+'_rows']:
            raise ValueError('Snapshot row count mismatch')
        frames.append(combined)
    return *frames, manifest


def collect_market_extension(client, codes, dates, directory, *, binding, interval=.5, clock=time.time, sleep=time.sleep):
    directory = Path(directory)
    identity = _request_identity(codes, dates, binding)
    complete = directory/'acquisition_manifest.json'
    if complete.exists():
        _, _, manifest = load_snapshot(directory, binding)
        if any(manifest[key] != identity[key] for key in identity):
            raise ValueError('Completed acquisition request mismatch')
        return manifest
    directory.mkdir(parents=True, exist_ok=True)
    request_path = directory/'request_identity.json'
    if request_path.exists():
        if read(request_path) != identity:
            raise ValueError('Partial acquisition request identity mismatch')
    else:
        write(request_path, identity, exclusive=True)
    requests = Requests(directory, interval, clock, sleep)
    combined = {}
    for kind in ('daily', 'factor'):
        parts = []
        for code in codes:
            for year in sorted({value[:4] for value in dates}):
                expected = [value for value in dates if value.startswith(year)]
                shard = directory/f'{kind}_{code}_{year}.parquet'
                proof = shard.with_suffix('.json')
                params = dict(ts_code=code, start_date=expected[0].replace('-',''),
                              end_date=expected[-1].replace('-',''), fields=','.join(DAILY if kind=='daily' else FACTOR))
                shard_identity = dict(kind=kind, params=params, dates=expected, binding=binding)
                if shard.exists() or proof.exists():
                    if not (shard.is_file() and proof.is_file()):
                        raise ValueError('Partial shard preserved; use a fresh acquisition directory')
                    saved = read(proof)
                    if saved.get('identity') != shard_identity or saved.get('sha256') != digest(shard):
                        raise ValueError('Shard identity/hash mismatch')
                    frame = _market(pd.read_parquet(shard), code, expected, kind)
                else:
                    response = requests.call(client.daily if kind == 'daily' else client.adj_factor, params)
                    try:
                        frame = _market(response, code, expected, kind)
                    except (ValueError, TypeError, KeyError):
                        requests.state['failure'] = 'SCHEMA'
                        write(requests.path, requests.state)
                        raise ValueError('ACQUISITION_SCHEMA: invalid coverage or values; evidence retained') from None
                    frame.to_parquet(shard, index=False)
                    write(proof, dict(identity=shard_identity, sha256=digest(shard)), exclusive=True)
                parts.append(frame)
        combined[kind] = pd.concat(parts, ignore_index=True)
        path = directory/('daily.parquet' if kind == 'daily' else 'factors.parquet')
        if path.exists():
            # Finalization interrupted: retain it and compare rather than overwrite.
            pd.testing.assert_frame_equal(pd.read_parquet(path), combined[kind])
        else:
            combined[kind].to_parquet(path, index=False)
    manifest = dict(identity, source='tushare', volume_unit='lots', amount_unit='thousand_CNY',
                    daily_rows=len(combined['daily']), factor_rows=len(combined['factor']),
                    daily_sha256=digest(directory/'daily.parquet'), factor_sha256=digest(directory/'factors.parquet'),
                    acquired_at=datetime.fromtimestamp(clock(), TZ).isoformat())
    write(complete, manifest, exclusive=True)
    load_snapshot(directory, binding)
    return manifest


def collect_calendar(client, start, end, directory, *, interval=.5, clock=time.time, sleep=time.sleep):
    start_day, end_day = day(start), day(end)
    if not 0 <= (end_day-start_day).days <= 10000:
        raise ValueError('Invalid calendar range')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path, manifest_path = directory/'calendar.json', directory/'calendar_manifest.json'
    if path.exists() or manifest_path.exists():
        if not (path.is_file() and manifest_path.is_file()) or digest(path) != read(manifest_path).get('calendar_sha256'):
            raise ValueError('Partial or changed calendar snapshot')
        body = read(path)
        if body.get('start') != start or body.get('end') != end or body.get('exchange') != 'SZSE':
            raise ValueError('Calendar request mismatch')
        return dict(body, calendar_sha256=digest(path))
    frame = Requests(directory, interval, clock, sleep).call(client.trade_calendar,
        dict(exchange='SZSE', start_date=start.replace('-',''), end_date=end.replace('-',''), fields='exchange,cal_date,is_open'))
    if frame is None or frame.empty or not {'exchange','cal_date','is_open'}.issubset(frame):
        raise ValueError('Calendar unavailable')
    dates = pd.to_datetime(frame.cal_date.astype(str), errors='raise').dt.strftime('%Y-%m-%d')
    expected = pd.date_range(start,end).strftime('%Y-%m-%d').tolist()
    opened = pd.to_numeric(frame.is_open, errors='raise')
    if (set(frame.exchange) != {'SZSE'} or sorted(dates.tolist()) != expected
            or not opened.isin([0,1]).all() or any(isinstance(v, bool) for v in frame.is_open)):
        raise ValueError('Calendar coverage/exchange/flags mismatch')
    rows = sorted([dict(cal_date=d, is_open=int(v)) for d,v in zip(dates,opened)], key=lambda row:row['cal_date'])
    body = dict(source='tushare.trade_cal', exchange='SZSE', start=start, end=end, rows=rows,
                acquired_at=datetime.fromtimestamp(clock(), TZ).isoformat())
    write(path, body, exclusive=True)
    write(manifest_path, dict(calendar_sha256=digest(path)), exclusive=True)
    return dict(body, calendar_sha256=digest(path))
