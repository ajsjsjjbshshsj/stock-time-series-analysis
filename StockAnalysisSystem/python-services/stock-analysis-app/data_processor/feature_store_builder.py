"""Explicit bounded rebuild, isolated from publication and model training."""
import hashlib
import json
from pathlib import Path

import pandas as pd

from data_processor.feature_store_contract import feature_records, validate_build_id, columns_digest, STORAGE_CONTRACT
from data_processor.feature_store_source import (SOURCE_COLUMNS, canonical_bytes, file_digest,
    snapshot_frames,snapshot_source,validate_source,verify_snapshot)


def algorithm_identity():
    """Normalize source newlines, not machine roots, when recording identity."""
    import numpy as np
    import talib
    from config.settings import TECHNICAL_INDICATORS
    from data_processor.feature_engineer import FEATURE_VERSION
    app=Path(__file__).resolve().parents[1]
    names=('data_processor/feature_engineer.py','data_processor/feature_schema.py',
           'data_processor/feature_store_contract.py')
    evidence=dict(files={n:hashlib.sha256((app/n).read_text(encoding='utf-8').encode()).hexdigest() for n in names},
                  parameters=TECHNICAL_INDICATORS,dependencies=dict(numpy=np.__version__,pandas=pd.__version__,talib=talib.__version__))
    return FEATURE_VERSION,hashlib.sha256(canonical_bytes(evidence)).hexdigest(),evidence


def compute_build(db,manifest,build_id,*,directory,repository=None,engineer=None,on_progress=None):
    """Compute a sealed snapshot; no call here marks READY or changes active input."""
    validate_build_id(build_id)
    directory=Path(directory).resolve()
    verify_snapshot(manifest,directory)
    if repository is None:
        from database.feature_store_repository import FeatureStoreRepository
        repository=FeatureStoreRepository(db)
    if engineer is None:
        from data_processor.feature_engineer import FeatureEngineer
        engineer=FeatureEngineer()
    version,code_hash,algorithm=algorithm_identity()
    repository.start_build(build_id,manifest,version,code_hash)
    total=0;digest=hashlib.sha256();stocks=[]
    try:
        for shard in manifest['shards']:
            source=validate_source(pd.read_parquet(directory/shard['file']),manifest['source_start'],manifest['source_cutoff'])
            calculation=source.copy(deep=True)
            for name in SOURCE_COLUMNS[2:]:
                calculation[name]=pd.to_numeric(calculation[name],errors='raise').astype(float)
            featured=engineer.calculate_all_features(calculation)
            before=list(zip(calculation.ts_code,calculation.trade_date))
            after=list(zip(featured.ts_code,featured.trade_date))
            if before!=after or len(featured)!=len(source):
                raise ValueError('Feature source-key coverage mismatch')
            rows=feature_records(featured,build_id,start_date=manifest['source_start'],cutoff=manifest['source_cutoff'])
            for offset in range(0,len(rows),1000):
                batch=rows[offset:offset+1000]
                if repository.insert_build_rows(build_id,batch)!=len(batch):
                    raise ValueError('Feature insert count mismatch')
            for row in rows:
                digest.update(canonical_bytes(row)+b'\n')
            total+=len(rows)
            stocks.append(dict(ts_code=shard['ts_code'],row_count=len(rows),last_date=shard['last_date']))
            if on_progress is not None:
                on_progress(dict(build_id=build_id,stocks=len(stocks),rows=total))
        if total!=manifest['row_count'] or len(stocks)!=manifest['stock_count']:
            raise ValueError('Feature build coverage mismatch')
        report=dict(build_id=build_id,storage_contract=STORAGE_CONTRACT,columns_sha256=columns_digest(),
                    algorithm_version=version,code_sha256=code_hash,algorithm=algorithm,
                    source_sha256=manifest['source_sha256'],source_start=manifest['source_start'],
                    source_cutoff=manifest['source_cutoff'],output_sha256=digest.hexdigest(),
                    row_count=total,stock_count=len(stocks),stocks=stocks,published=False)
        repository.finish_build(build_id,report)
        with (directory.parent/('build_'+build_id+'.json')).open('x',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2,allow_nan=False)
        return report
    except Exception:
        repository.fail_build(build_id,'FEATURE_BUILD_FAILED')
        raise ValueError('Feature build failed; evidence retained, no publication') from None
