"""Exact audited inference-only code migrations, never a general legacy bypass."""
from pathlib import Path
from tasks.artifacts import read_json


def require_compatible(algorithm,operation,frozen,current):
    if algorithm not in ('xgboost','lightgbm','transformer'):raise ValueError('Unknown model algorithm')
    if frozen==current:return
    if (operation not in ('predict','backtest') or not isinstance(frozen,dict) or not isinstance(current,dict)
            or set(frozen)!= {'sha256','dependencies'} or set(current)!= {'sha256','dependencies'}
            or frozen['dependencies']!=current['dependencies']):raise ValueError('Model algorithm/dependencies changed; explicit retraining required')
    certificate=read_json(Path(__file__).with_suffix('.json'),max_bytes=16384)
    if set(certificate)!= {'schema_version','pairs'} or certificate['schema_version']!=1:raise ValueError('Invalid compatibility certificate')
    pair=dict(algorithm=algorithm,before=frozen['sha256'],after=current['sha256'],reason='checkpoint_and_input_reuse_only')
    if pair not in certificate['pairs']:raise ValueError('Unaudited model pipeline change; explicit retraining required')
