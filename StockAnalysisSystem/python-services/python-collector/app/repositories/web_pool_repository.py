"""Read-first local pool selection over the existing immutable pool registry."""
from collections import Counter
from datetime import datetime,time,timedelta
import re
from sqlalchemy import text
from app.preparation.contracts import TZ, identity, iso
from app.preparation.local_pools import freeze_local, INDEX_COUNTS
from app.nightly.contracts import is_a_share
from .pool_repository import PoolRepository
from ._preparation_sql import transaction
from .web_collection_repository import page


class PoolUnavailable(LookupError): pass
class PoolConflict(ValueError): pass


def version(value):
    if type(value) is not str or re.fullmatch(r'(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})', value) is None:
        raise ValueError('INVALID_POOL_VERSION')
    return value


def summary(pool):
    return {k:pool[k] for k in ('pool_version', 'pool_id', 'display_name', 'group_type', 'group_code',
        'provider', 'requested_as_of', 'provider_snapshot_date', 'acquired_at', 'stock_count',
        'member_sha256', 'schema_version', 'survivorship_bias')}


class WebPools:
    def __init__(self, session_factory, *, clock=None):
        self.factory = session_factory
        self.registry = PoolRepository(session_factory)
        self.clock = clock or (lambda: datetime.now(TZ))

    @staticmethod
    def _basic(session):
        rows = session.execute(text('SELECT ts_code,symbol,name,industry,list_date FROM stock_basic ORDER BY ts_code')).mappings().all()
        result = [{k:str(v) if v is not None else None for k,v in r.items()} for r in rows if is_a_share(r['ts_code'])]
        if len({r['ts_code'] for r in result}) != len(result): raise ValueError('DUPLICATE_REFERENCE')
        return result

    def catalog(self):
        with transaction(self.factory) as session:
            basic = self._basic(session)
            groups = session.execute(text("SELECT group_code,MAX(as_of_date) AS latest FROM stock_constituent WHERE group_type='index' GROUP BY group_code")).mappings().all()
        counts = Counter(r['industry'] for r in basic if r['industry'])
        now=self.clock().astimezone(TZ)
        cutoff=now.date() if now.time()>time(16) else now.date()-timedelta(days=1)
        return dict(market_count=len(basic), industries=[dict(code=k,count=counts[k]) for k in sorted(counts)],
            indexes=[dict(code=c, name=n, expected_count=INDEX_COUNTS[c], local_snapshot_date=next((str(r['latest']) for r in groups if r['group_code']==c),None))
                for c,n in (('000300.SH','沪深300'),('000852.SH','中证1000'))],
            membership_policy='fixed_snapshot_not_historical_constituents', today=now.date().isoformat(),completed_date=cutoff.isoformat())

    def candidate(self, kind, code, *, as_of=None):
        now = self.clock().astimezone(TZ)
        requested = as_of or now.date().isoformat()
        if iso(requested) > now.date(): raise ValueError('FUTURE_POOL_DATE')
        if kind not in ('market','industry','index') or type(code) is not str or not code or len(code)>128:
            raise ValueError('INVALID_SELECTOR')
        if kind != 'index' and requested != now.date().isoformat(): raise ValueError('LOCAL_CLASSIFICATION_IS_CURRENT_ONLY')
        if kind == 'market' and code != 'SH_SZ_A' or kind == 'index' and code not in INDEX_COUNTS:
            raise ValueError('INVALID_SELECTOR')
        source_date = None
        with transaction(self.factory) as session:
            if kind == 'index':
                raw_date = session.execute(text("SELECT MAX(as_of_date) FROM stock_constituent WHERE group_type='index' AND group_code=:code AND as_of_date<=:day"),dict(code=code,day=requested)).scalar_one()
                if raw_date is None: raise PoolUnavailable('INDEX_NOT_STORED')
                source_date = str(raw_date)
                rows = session.execute(text("SELECT ts_code,weight,source FROM stock_constituent WHERE group_type='index' AND group_code=:code AND as_of_date=:day ORDER BY ts_code"),dict(code=code,day=source_date)).mappings().all()
                if len(rows)!=INDEX_COUNTS[code] or any(not is_a_share(r['ts_code']) for r in rows):
                    raise PoolUnavailable('INDEX_NOT_COMPLETE')
                members = [dict(ts_code=r['ts_code'],weight=float(r['weight']) if r['weight'] is not None else None) for r in rows]
                source_rows = [{k:str(v) if v is not None else None for k,v in r.items()} for r in rows]
            else:
                rows = self._basic(session)
                if kind == 'industry': rows = [r for r in rows if r['industry']==code]
                if not rows: raise PoolUnavailable('POOL_EMPTY')
                members = [dict(ts_code=r['ts_code'],weight=None) for r in rows]
                source_rows = rows
        provider = 'local.stock_constituent' if kind=='index' else 'local.stock_basic'
        display = {'market':'沪深A股全市场','index':{'000300.SH':'沪深300','000852.SH':'中证1000'}.get(code),'industry':code}[kind]
        metadata = dict(pool_id=kind+':'+code,display_name=display,group_type=kind,group_code=code,provider=provider,
            requested_as_of=requested,provider_snapshot_date=source_date,acquired_at=now.isoformat())
        return freeze_local(metadata,members,identity(dict(provider=provider,date=source_date,rows=source_rows)))

    def get(self, selected):
        version(selected)
        try: result = self.registry.resolve(pool_version=selected)
        except ValueError as error:
            if str(error)=='POOL_NOT_FOUND': raise PoolUnavailable('POOL_NOT_FOUND') from None
            raise RuntimeError('STORED_POOL_INVALID') from None
        if any(not is_a_share(c) for c in result['codes']): raise PoolUnavailable('SAVED_POOL_OUTSIDE_SCOPE')
        return result

    def save(self, kind, code, expected, *, as_of=None):
        version(expected)
        pool = self.candidate(kind,code,as_of=as_of)
        if pool['pool_version'] != expected: raise PoolConflict('MEMBERSHIP_CHANGED')
        try: return self.registry.register(pool,activate=False)
        except RuntimeError as error:
            if str(error)!='STORAGE_UNAVAILABLE': raise
            # Concurrent content-addressed saves may race on the unique version.
            # Resolve in a fresh transaction; never accept a mismatched record.
            result = self.get(expected)
            if result['member_sha256'] != pool['member_sha256']: raise PoolConflict('SAVED_MEMBERS_CHANGED')
            return result

    def versions(self, limit=20, offset=0):
        page(limit,offset)
        with transaction(self.factory) as session:
            ids = session.execute(text('SELECT pool_version FROM stock_pool_version ORDER BY created_at DESC,pool_version LIMIT :n OFFSET :o'),dict(n=limit,o=offset)).scalars().all()
            total = session.execute(text('SELECT COUNT(*) FROM stock_pool_version')).scalar_one()
        return dict(items=[summary(self.get(id)) for id in ids],total=total)

    def members(self, selected, *, limit=20, offset=0):
        page(limit,offset); pool=self.get(selected)
        codes=pool['codes'][offset:offset+limit]
        return dict(pool=summary(pool), items=pool['members'][offset:offset+limit],total=len(pool['codes']),offset=offset)
