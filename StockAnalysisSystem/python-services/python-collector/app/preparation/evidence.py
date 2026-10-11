"""Content-sealed, shared source files. Registry hits are revalidated on load."""
from collections import defaultdict
from pathlib import Path
import json
import pandas as pd

from app.market_data.forward_snapshot import read,write,digest
from app.market_data.repair_contract import request
from app.market_data.repair_acquisition import validate_rows
from app.market_data.repair_snapshot import verify_snapshot
from app.repositories.source_evidence_repository import validate_ref,FIELDS
from .contracts import identity,iso,CODE

METHODS = ('daily','daily_basic','adj_factor','suspend_d')


def validate_bundle(bundle):
    required={'kind','provider','start_date','end_date','expected_codes','rows'}
    if set(bundle)!=required or bundle['kind'] not in METHODS or bundle['provider']!='tushare.'+bundle['kind']:
        raise ValueError('SOURCE_BUNDLE_FIELDS')
    codes=bundle['expected_codes']; start,end=bundle['start_date'],bundle['end_date']
    if (not isinstance(codes,list) or not codes or codes!=sorted(set(codes))
            or any(type(c) is not str or CODE.fullmatch(c) is None for c in codes) or iso(start)>iso(end)
            or not isinstance(bundle['rows'],list)):
        raise ValueError('SOURCE_BUNDLE_IDENTITY')
    rows=[]
    by_day=defaultdict(list)
    for raw in bundle['rows']:
        row=dict(raw)
        day=str(row['trade_date'])
        if len(day)==10: day=iso(day).strftime('%Y%m%d')
        row['trade_date']=day
        by_day[day].append(row); rows.append(row)
    r=request(bundle['kind'],codes,start,end)
    # Aggregated legacy evidence is validated per original date capacities, not
    # incorrectly treated as a single 3-year full-market response.
    if bundle['kind']=='adj_factor': validate_rows(rows,r)
    else:
        for daily in by_day.values(): validate_rows(daily,r)
    return dict(bundle,rows=rows)


class EvidenceStore:
    def __init__(self,data_root,repository):
        self.root=Path(data_root).resolve()
        self.repository=repository

    def _path(self,relative):
        if not isinstance(relative,str) or ':' in relative or '\\' in relative or '..' in Path(relative).parts:
            raise ValueError('EVIDENCE_PATH_INVALID')
        result=(self.root/relative).resolve()
        if Path(relative).is_absolute() or not result.is_relative_to(self.root):
            raise ValueError('EVIDENCE_PATH_OUTSIDE_ROOT')
        return result

    def register_verified(self,bundle):
        bundle=validate_bundle(bundle)
        content=identity(bundle)
        relative=f'bundles/{content}/manifest.json'
        path=self._path(relative)
        complete=path.parent/'complete.json'
        manifest=dict(schema_version=1,content_sha256=content,bundle=bundle)
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if read(path)!=manifest or not complete.exists() or read(complete)!=dict(manifest_sha256=digest(path)):
                raise ValueError('PARTIAL_OR_CHANGED_EVIDENCE')
        else:
            write(path,manifest,exclusive=True)
            write(complete,dict(manifest_sha256=digest(path)),exclusive=True)
        refs=[]
        manifest_sha=digest(path)
        for code in bundle['expected_codes']:
            ref=dict(kind=bundle['kind'],ts_code=code,start_date=bundle['start_date'],end_date=bundle['end_date'],
                provider=bundle['provider'],relative_path=relative,manifest_sha256=manifest_sha,content_sha256=content)
            refs.append(ref)
        return self.repository.register_many(refs)

    def load(self,references,*,codes,start,end):
        result={method:[] for method in METHODS}
        result['queried_suspensions']=[]
        loaded={}; seen={method:{} for method in METHODS}; queried=set()
        for reference in references:
            path=self._path(reference['relative_path'])
            ref={k:reference[k] for k in FIELDS}
            validate_ref(ref)
            if ref['ts_code'] not in codes: continue
            if path not in loaded:
                if not path.is_file() or digest(path)!=ref['manifest_sha256']:
                    raise ValueError('EVIDENCE_MANIFEST_HASH')
                if read(path.parent/'complete.json')!=dict(manifest_sha256=digest(path)):
                    raise ValueError('EVIDENCE_COMPLETION_HASH')
                manifest=read(path)
                body=validate_bundle(manifest['bundle'])
                if manifest!=dict(schema_version=1,content_sha256=identity(body),bundle=body):
                    raise ValueError('EVIDENCE_CONTENT_HASH')
                by_stock=defaultdict(list)
                for row in body['rows']: by_stock[row['ts_code']].append(row)
                loaded[path]=(body,manifest['content_sha256'],digest(path),by_stock)
            body,body_hash,manifest_hash,by_stock=loaded[path]
            if (body_hash!=ref['content_sha256'] or manifest_hash!=ref['manifest_sha256'] or body['kind']!=ref['kind']
                    or body['provider']!=ref['provider'] or ref['ts_code'] not in body['expected_codes']
                    or (body['start_date'],body['end_date'])!=(ref['start_date'],ref['end_date'])):
                raise ValueError('EVIDENCE_INDEX_BINDING')
            method=body['kind']
            # Native suspend_d queries are single-date. Aggregated legacy ranges
            # do not prove that every member/day was queried, even when empty.
            if method=='suspend_d' and body['start_date']==body['end_date'] and start<=body['start_date']<=end:
                queried.add((ref['ts_code'],body['start_date']))
            for row in by_stock.get(ref['ts_code'],[]):
                day=row['trade_date']; normalized=f'{day[:4]}-{day[4:6]}-{day[6:]}'
                if row['ts_code']!=ref['ts_code'] or not start<=normalized<=end: continue
                value=dict(row,trade_date=normalized)
                key=(row['ts_code'],normalized,row.get('suspend_type'),row.get('suspend_timing'))
                old=seen[method].get(key)
                if old is not None and old!=value: raise ValueError('SOURCE_EVIDENCE_CONFLICT')
                seen[method][key]=value
        for method in METHODS:
            result[method]=[seen[method][k] for k in sorted(seen[method],key=lambda k:tuple(str(v) for v in k))]
        result['queried_suspensions']=sorted(queried)
        return result

    def import_legacy_b1(self,snapshot_path):
        snapshot_path=Path(snapshot_path)
        verified=verify_snapshot(snapshot_path)
        def verified_rows(name):
            path=snapshot_path/(name+'.parquet')
            expected=verified['manifest']['files'][name+'.parquet']
            if digest(path)!=expected: raise ValueError('LEGACY_SOURCE_CHANGED')
            rows=pd.read_parquet(path).astype(object)
            rows=rows.where(rows.notna(),None).to_dict('records')
            if digest(path)!=expected: raise ValueError('LEGACY_SOURCE_CHANGED')
            return rows
        report=verified['report']; codes=sorted({r['ts_code'] for r in verified_rows('status')})
        refs=[]
        for method,output in (('daily','daily'),('daily_basic','basic'),('adj_factor','factors'),('suspend_d','suspensions')):
            if method in ('daily','daily_basic'):
                # Merged outputs include old SQL rows, not raw provider responses.
                # Only factor/suspension source columns are reusable evidence here.
                continue
            refs.extend(self.register_verified(dict(kind=method,provider='tushare.'+method,
                start_date=report['start'],end_date=report['end'],expected_codes=codes,rows=verified_rows(output))))
        return dict(references=refs,source_issue_counts=report['source_issue_counts'])
