"""Append-only local reports for CLI validation. Never replace prior results."""
from datetime import datetime, date, timezone
from pathlib import Path
from uuid import uuid4
import json
import math
import numpy as np
import pandas as pd


def _json(value):
    if isinstance(value, pd.DataFrame):
        return dict(rows=_json(value.to_dict('records')), metadata=_json(value.attrs))
    if isinstance(value, pd.Series):
        return [dict(date=_json(index), value=_json(item)) for index, item in value.items()]
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.ndarray): return _json(value.tolist())
    if isinstance(value, np.generic): return _json(value.item())
    if isinstance(value, dict): return {str(k): _json(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)): return [_json(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value): return None
    if value is pd.NA or value is pd.NaT: return None
    if value is None or isinstance(value, (str, int, float, bool)): return value
    raise ValueError('Unsupported validation report value')


def export_strategy_result(result, directory, strategy_id):
    content = json.dumps(dict(schema_version=1, strategy_id=strategy_id,
        created_at=datetime.now(timezone.utc).isoformat(), result=_json(result)),
        ensure_ascii=False, allow_nan=False, indent=2)
    root = Path(directory) / (datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')+'_'+uuid4().hex)
    root.mkdir(parents=True, exist_ok=False)
    path = root / 'result.json'
    with path.open('x', encoding='utf-8') as stream:
        stream.write(content)
    print('STRATEGY_REPORT '+str(path.resolve()))
    summary = result if isinstance(result, dict) else result.attrs
    print(json.dumps(_json({k: summary[k] for k in ('metrics', 'data_selection', 'model_metadata')
                           if k in summary}), ensure_ascii=False, allow_nan=False))
    return path
