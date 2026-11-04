"""Reuse RAW-scale feature rows only for an identical full stock history/algorithm."""
import pandas as pd
from tasks.reuse import read_parquet
from tasks.artifacts import file_digest
from data_processor.feature_store_contract import FEATURE_COLUMNS,FEATURE_INPUTS,columns_digest,feature_records


def identity(shard,version,code_hash):
    return dict(kind='raw_feature_stock_v1',ts_code=shard['ts_code'],source_sha256=shard['source_sha256'],
        row_count=shard['row_count'],algorithm_version=version,code_sha256=code_hash,columns_sha256=columns_digest())


def load(store,key,source,build_id,start,cutoff):
    value=store.get(key)
    if value is None:return None
    folder,metadata=value
    frame=read_parquet(folder/'features.parquet',max_rows=len(source),max_columns=2+len(FEATURE_COLUMNS))
    if (metadata!={'row_count':len(source)} or list(frame.columns)!=['ts_code','trade_date',*FEATURE_COLUMNS]
            or list(zip(frame.ts_code,pd.to_datetime(frame.trade_date).dt.date))!=list(zip(source.ts_code,source.trade_date))):
        raise ValueError('Reusable feature stock keys changed')
    adapted=frame.rename(columns=dict(zip(FEATURE_COLUMNS,FEATURE_INPUTS)))
    return feature_records(adapted,build_id,start_date=start,cutoff=cutoff)


def save(store,key,rows):
    def build(folder):
        frame=pd.DataFrame([{name:row[name] for name in ('ts_code','trade_date',*FEATURE_COLUMNS)} for row in rows])
        frame.to_parquet(folder/'features.parquet',index=False)
        return dict(row_count=len(rows))
    folder,_=store.publish(key,build)
    return dict(ts_code=key['ts_code'],cache_identity=key,manifest_sha256=file_digest(folder/'manifest.json'))
