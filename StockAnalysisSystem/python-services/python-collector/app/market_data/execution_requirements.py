"""Read a sealed evaluator gap list. Never treat a plan as evidence of prices."""
import hashlib,json
from pathlib import Path
import re
import pandas as pd
import pyarrow.parquet as pq
from .execution_evidence import _day


def read_requirements(path):
    path=Path(path).absolute();root=path.parent
    if path.name!='requirements.parquet' or any(p.is_symlink() or bool(getattr(p.lstat(),'st_file_attributes',0)&0x400)
            for p in (path,*path.parents) if p.exists()):raise ValueError('EXECUTION_PLAN_PATH')
    try:
        manifest_path=root/'manifest.json';complete_path=root/'complete.json'
        for p in (manifest_path,complete_path):
            if p.is_symlink() or p.stat().st_size>8*1024*1024:raise ValueError('EXECUTION_PLAN_SEAL')
        manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
        complete=json.loads(complete_path.read_text(encoding='utf-8'))
        digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
        if complete!=dict(manifest_sha256=digest(manifest_path)) or manifest['schema_version']!=1:
            raise ValueError('EXECUTION_PLAN_SEAL')
        if manifest['files'].get(path.name)!=digest(path):raise ValueError('EXECUTION_PLAN_CHANGED')
        metadata=pq.read_metadata(path)
        if metadata.num_rows>2000000 or path.stat().st_size>128*1024*1024:raise ValueError('EXECUTION_PLAN_BOUND')
        frame=pd.read_parquet(path)
        if set(frame)!={'kind','ts_code','trade_date'} or frame.columns.duplicated().any() or frame.isna().any().any():
            raise ValueError('EXECUTION_PLAN_FIELDS')
        for row in frame.itertuples():
            if row.kind not in ('stk_limit','adj_factor') or type(row.ts_code) is not str or not re.fullmatch(r'\d{6}\.(SH|SZ)',row.ts_code):
                raise ValueError('EXECUTION_PLAN_UNSUPPORTED_GAP')
            _day(row.trade_date)
        if frame.duplicated(['kind','ts_code','trade_date']).any():raise ValueError('EXECUTION_PLAN_DUPLICATE')
        return frame
    except (OSError,KeyError,TypeError) as exc:raise ValueError('EXECUTION_PLAN_UNVERIFIED') from None
