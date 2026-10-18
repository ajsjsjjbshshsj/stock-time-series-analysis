"""One explicit CPU task. Success means reload-verified candidate, never release."""
import time
import json
from .contracts import normalize_request
from .artifacts import task_path,task_digest,write_json,file_digest,verify_candidate,FILES


def run_xgboost(task,root,source,emit):
    import numpy as np
    import xgboost
    import pandas as pd
    from analysis.predictor import StockPredictor
    from analysis.strategy_samples import dated_samples,purged_splits
    request=normalize_request(task['request']);path=task_path(root,task['id'])
    if path.exists():raise ValueError('Candidate directory already exists')
    emit('PREFLIGHT');started=time.monotonic()
    frame,calendar,contract=source.load(task)
    names=list(contract['feature_names'])
    emit('INPUT')
    params=dict(n_estimators=request['n_estimators'],max_depth=request['max_depth'],n_jobs=1,
                random_state=42,tree_method='hist',device='cpu')
    samples=dated_samples(frame,names,horizon=1,classification=True)
    groups=purged_splits(samples)
    if any(groups[k].label.nunique()!=2 for k in ('train','val','test')):raise ValueError('Two target classes required in each split')
    emit('TRAIN');predictor=StockPredictor('xgboost')
    result=predictor.train_dated(frame,feature_names=names,calendar=calendar,model_params=params)
    metrics={k:float(result['metrics'][k]) for k in ('Accuracy','Precision','Recall','F1','AUC')}
    if not metrics or not all(np.isfinite(v) for v in metrics.values()):raise ValueError('Non-finite metrics')
    emit('VERIFY')
    probe=groups['test'][names].head(16)
    predictions=result['model'].predict(probe)
    if not np.isfinite(predictions).all():raise ValueError('Invalid model predictions')
    metadata=dict(schema_version=1,task_sha256=task_digest(task),purpose='engineering_smoke',
        feature_names=names,params=result['metadata']['engineering_params'],metrics=metrics,training=result['metadata'],source=task['binding'],
        booster_config=json.loads(result['model'].get_booster().save_config()),
        dependencies=dict(xgboost=xgboost.__version__,pandas=pd.__version__,numpy=np.__version__),
        confusion_matrix=result['metrics']['Confusion_Matrix'].tolist(),elapsed_seconds=time.monotonic()-started)
    path.parent.mkdir(parents=True,exist_ok=True);path.mkdir()
    result['model'].get_booster().set_attr(training_task_hash=task_digest(task))
    result['model'].save_model(path/'model.json')
    write_json(path/'metadata.json',metadata)
    write_json(path/'probe.json',dict(values=probe.to_numpy(dtype=float).tolist(),predictions=predictions.astype(float).tolist()))
    write_json(path/'manifest.json',dict(schema_version=1,task_sha256=task_digest(task),files={name:file_digest(path/name) for name in FILES}))
    summary=verify_candidate(task,root);emit('SAVE')
    return summary
