"""Collect real factors for an audited daily panel; no database writes."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    import pandas as pd
    import tushare as ts
    from app.config import TUSHARE_TOKEN,COLLECTION_CONFIG
    from app.market_data.factor_snapshot import collect_factor_snapshot
    source=args.source.resolve()
    output=args.output.resolve()
    if source.is_relative_to(output):
        raise ValueError('Source must be outside factor output directory')
    manifest=json.loads(source.with_name('collection_manifest.json').read_text(encoding='utf-8'))
    digest=lambda path:hashlib.sha256(path.read_bytes()).hexdigest()
    if digest(source)!=manifest['snapshot_sha256']:
        raise ValueError('Source snapshot hash mismatch')
    if (output/'factors.parquet').exists():
        raise ValueError('Completed factor snapshot exists; do not overwrite')
    panel=pd.read_parquet(source)
    output.mkdir(parents=True,exist_ok=True)
    started=time.perf_counter()
    try:
        factors=collect_factor_snapshot(ts.pro_api(TUSHARE_TOKEN,timeout=20),panel,
            output/'factor_shards',interval=COLLECTION_CONFIG['request_interval'])
    except Exception as error:
        raise RuntimeError(str(error).replace(TUSHARE_TOKEN,'[REDACTED]')) from None
    factors.to_parquet(output/'factors.parquet',index=False)
    metadata=dict(source='tushare.adj_factor',factor_sha256=digest(output/'factors.parquet'),
        source_sha256=manifest['snapshot_sha256'],rows=len(factors),stocks=factors.ts_code.nunique(),
        first_date=str(factors.trade_date.min().date()),last_date=str(factors.trade_date.max().date()),
        volume_unit='lots',amount_unit='thousand_CNY',collection_seconds=time.perf_counter()-started)
    (output/'factor_manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'FACTOR_COLLECTION_COMPLETE rows={len(factors)} stocks={factors.ts_code.nunique()}',flush=True)


if __name__=='__main__':
    main()
