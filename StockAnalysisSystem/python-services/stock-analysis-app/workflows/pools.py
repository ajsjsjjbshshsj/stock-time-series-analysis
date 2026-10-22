"""Saved membership bindings. No source acquisition, feature or model imports."""
import re
from tasks.contracts import digest

FIELDS=('schema_version','pool_version','member_sha256','stock_count','stock_codes','stockid2idx',
        'group_type','group_code','provider','provider_snapshot_date')
VERSION=r'(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})'


def validate_selection(value):
    if not isinstance(value,dict) or set(value)!={*FIELDS,'selection_sha256'}:
        raise ValueError('Invalid registry pool binding')
    codes=value['stock_codes'];mapping=value['stockid2idx']
    if (type(value['schema_version']) is not int or value['schema_version']!=1
            or type(value['stock_count']) is not int or not 1<=value['stock_count']<=10000
            or not isinstance(value['pool_version'],str) or re.fullmatch(VERSION,value['pool_version']) is None
            or not isinstance(codes,list) or any(type(c) is not str or re.fullmatch(r'[0-9]{6}\.(SH|SZ)',c) is None for c in codes)
            or codes!=sorted(set(codes)) or len(codes)!=value['stock_count']
            or type(mapping) is not dict or any(type(v) is not int for v in mapping.values())
            or mapping!={c:i for i,c in enumerate(codes)}
            or type(value['member_sha256']) is not str or re.fullmatch('[0-9a-f]{64}',value['member_sha256']) is None
            or value['group_type'] not in ('market','index','industry')
            or type(value['group_code']) is not str or not value['group_code']
            or value['provider'] not in ('local.stock_basic','local.stock_constituent','tushare.index_weight','akshare.eastmoney')
            or value['selection_sha256']!=digest({k:value[k] for k in FIELDS})):
        raise ValueError('Registry pool identity changed')
    if value['provider_snapshot_date'] is not None:
        from datetime import date
        if date.fromisoformat(value['provider_snapshot_date']).isoformat()!=value['provider_snapshot_date']:
            raise ValueError('Invalid pool snapshot date')
    return value


class PoolSelection:
    def __init__(self,engine):self.engine=engine

    def resolve(self,version):
        if type(version) is not str or re.fullmatch(VERSION,version) is None:
            raise ValueError('Invalid pool version')
        from api.collector import collector_imports
        collector_imports()
        from sqlalchemy.orm import sessionmaker
        from app.repositories.web_pool_repository import WebPools
        pool=WebPools(sessionmaker(bind=self.engine)).get(version)
        value={key:pool[key] for key in FIELDS if key not in ('schema_version','stock_codes')}
        value.update(schema_version=1,stock_codes=list(pool['codes']))
        value['selection_sha256']=digest(value)
        return validate_selection(value)


def select_codes(request,proof):
    codes=validate_selection(proof)['stock_codes']
    if request['algorithm']=='lightgbm':return list(codes)
    if request['algorithm']=='xgboost':
        if request.get('ts_code') not in codes:raise ValueError('Stock is not in selected pool')
        return [request['ts_code']]
    if request['algorithm']=='transformer':
        if request.get('stock_codes')!=codes or not 10<=len(codes)<=20:
            raise ValueError('Transformer requires the exact fixed10–20 stock pool')
        return list(codes)
    raise ValueError('Unknown pool algorithm')


def workflow_selection(request,resolver,model_binding=None):
    """New training selects; later operations inherit, never relabel a model."""
    selected=request.get('pool_version')
    if request['operation']=='train':
        proof=resolver.resolve(selected) if selected is not None else None
    else:
        frozen=(model_binding or {}).get('registry_pool')
        if frozen is None:
            if selected is not None:raise ValueError('Cannot attach a pool to an unbound model')
            return None
        validate_selection(frozen)
        if selected is not None and selected!=frozen['pool_version']:
            raise ValueError('Cannot replace the saved model pool')
        proof=resolver.resolve(frozen['pool_version'])
        if proof!=frozen:raise RuntimeError('Saved model registry pool changed')
    if proof is not None:select_codes(request,proof)
    return proof
