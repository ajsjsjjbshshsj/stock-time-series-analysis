"""Bounded pool source adapters; no credential or database initialization."""
from datetime import datetime
from pathlib import Path
import importlib.util
import math

import pandas as pd

from .contracts import TZ, CODE, freeze_pool, iso, validate_pool
from app.market_data.forward_snapshot import digest, read
from app.market_data.codes import to_ts_code

INDEXES = {'000300.SH': ('沪深300', 300), '000852.SH': ('中证1000', 1000)}
INDEX_FIELDS = ['index_code', 'con_code', 'trade_date', 'weight']


class PoolSources:
    def __init__(self, requests, tushare_client, akshare_client, *, clock=None):
        self.requests = requests
        self.tushare_client = tushare_client
        self.akshare_client = akshare_client
        self.clock = clock or (lambda: datetime.now(TZ))

    def acquire(self, group_type, group_code, as_of_date):
        stamp = iso(as_of_date)
        acquired = self.clock()
        if acquired.tzinfo is None or stamp > acquired.astimezone(TZ).date():
            raise ValueError('INVALID_POOL_SOURCE_TIME')
        metadata = dict(pool_id=f'{group_type}:{group_code}', group_type=group_type,
                        group_code=group_code, requested_as_of=as_of_date,
                        acquired_at=acquired.astimezone(TZ).isoformat(), provider_snapshot_date=None)
        if group_type == 'index':
            if group_code not in INDEXES:
                raise ValueError('UNSUPPORTED_INDEX')
            metadata.update(display_name=INDEXES[group_code][0], provider='tushare.index_weight')
            rows = self._index(group_code, stamp)
            metadata['provider_snapshot_date'] = datetime.strptime(rows[0]['trade_date'], '%Y%m%d').date().isoformat()
            members = [dict(ts_code=r['con_code'], weight=r['weight']) for r in rows]
        elif group_type == 'industry':
            metadata.update(display_name=group_code, provider='akshare.eastmoney')
            frame = self.akshare_client.industry_constituents(group_code)
            if frame is None or frame.empty or frame.columns.duplicated().any() or '代码' not in frame:
                raise ValueError('INDUSTRY_SOURCE_SCHEMA')
            members = []
            for value in frame['代码']:
                if not isinstance(value, str) or not value.isdigit() or len(value) != 6:
                    raise ValueError('INDUSTRY_MEMBER_CODE')
                members.append(dict(ts_code=to_ts_code(value)))
        else:
            raise ValueError('UNSUPPORTED_POOL_TYPE')
        return freeze_pool(metadata, members)

    def _index(self, code, requested):
        stamp = pd.Timestamp(requested)
        for offset in range(3):
            start = (stamp - pd.DateOffset(months=offset)).replace(day=1)
            end = min(start + pd.offsets.MonthEnd(0), stamp)
            params = dict(index_code=code, start_date=start.strftime('%Y%m%d'),
                          end_date=end.strftime('%Y%m%d'), fields=','.join(INDEX_FIELDS))
            frame = self.requests.call(self.tushare_client.index_weight, params)
            if frame is None or frame.columns.duplicated().any() or set(frame.columns) != set(INDEX_FIELDS):
                raise ValueError('INDEX_SOURCE_SCHEMA')
            if len(frame) >= 5000:
                raise ValueError('INDEX_SOURCE_TRUNCATED')
            if frame.empty:
                continue
            rows = frame[INDEX_FIELDS].to_dict('records')
            seen = set()
            for row in rows:
                day, member, weight = row['trade_date'], row['con_code'], row['weight']
                if (row['index_code'] != code or type(day) is not str or len(day) != 8
                        or not day.isdigit() or not params['start_date'] <= day <= params['end_date']
                        or type(member) is not str or CODE.fullmatch(member) is None
                        or type(weight) not in (float, int) or not math.isfinite(weight) or weight < 0):
                    raise ValueError('INDEX_SOURCE_IDENTITY')
                datetime.strptime(day, '%Y%m%d')
                if (day, member) in seen:
                    raise ValueError('INDEX_SOURCE_DUPLICATE')
                seen.add((day, member))
            latest = max(r['trade_date'] for r in rows)
            selected = [r for r in rows if r['trade_date'] == latest]
            if len(selected) != INDEXES[code][1]:
                raise ValueError('INDEX_SOURCE_INCOMPLETE')
            return selected
        raise ValueError('INDEX_SOURCE_EMPTY')


def import_legacy_pool(path):
    source = Path(path)
    verifier = Path(__file__).resolve().parents[4] / 'python-services/stock-analysis-app/analysis/csi300_universe.py'
    spec = importlib.util.spec_from_file_location('_preparation_legacy_pool', verifier)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    legacy = module.verify_universe(source)
    raw = read(source / 'source/acquisition_manifest.json')['rows']
    metadata = dict(pool_id='index:000300.SH', display_name='沪深300', group_type='index',
                    group_code='000300.SH', provider=legacy['source'],
                    requested_as_of=legacy['requested_as_of'],
                    provider_snapshot_date=legacy['provider_snapshot_date'], acquired_at=legacy['acquired_at'])
    pool = freeze_pool(metadata, [dict(ts_code=r['con_code'], weight=r['weight']) for r in raw])
    pool.update(pool_version=legacy['pool_version'],
                legacy_source_manifest_sha256=legacy['source_manifest_sha256'],
                legacy_manifest_sha256=digest(source / 'manifest.json'))
    return validate_pool(pool)
