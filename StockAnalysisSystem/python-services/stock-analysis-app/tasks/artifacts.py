"""Private engineering candidates, never the existing model registry/publication."""
import hashlib
import json
import os
from pathlib import Path
from .contracts import identity,digest,encoded,normalize_request

FILES=('model.json','metadata.json','probe.json')


def task_path(root,task_id):
    identity(task_id);root=Path(root).absolute()
    if any(p.is_symlink() for p in (root,*root.parents)):raise ValueError('Unsafe candidate root')
    root=root.resolve();path=root/task_id
    app=Path(__file__).resolve().parents[1]
    protected=app/'models'
    if root==app or root==app.parents[1] or root.is_relative_to(protected) or protected.is_relative_to(root):
        raise ValueError('Candidate root overlaps protected models')
    if path.is_symlink() or path.resolve().parent!=root:raise ValueError('Unsafe task path')
    return path


def file_digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def write_json(path,value):
    with Path(path).open('x',encoding='utf-8') as stream:
        stream.write(encoded(value));stream.flush();os.fsync(stream.fileno())


def read_json(path):
    path=Path(path)
    if path.is_symlink() or path.stat().st_size>2*1024*1024:raise ValueError('Unsafe candidate JSON')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:raise ValueError('Duplicate JSON key')
            result[k]=v
        return result
    return json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=unique,
        parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON')))


def task_digest(task):return digest(dict(id=identity(task['id']),request=normalize_request(task['request']),binding=task['binding']))


def verify_candidate(task,root):
    import numpy as np
    import pandas as pd
    from xgboost import XGBClassifier
    path=task_path(root,task['id'])
    if not path.is_dir() or {p.name for p in path.iterdir()}!={*FILES,'manifest.json'}:raise ValueError('Incomplete candidate')
    if any((path/name).is_symlink() for name in (*FILES,'manifest.json')):raise ValueError('Unsafe candidate file')
    manifest=read_json(path/'manifest.json')
    if (manifest.get('schema_version')!=1 or manifest.get('task_sha256')!=task_digest(task)
            or set(manifest.get('files',{}))!=set(FILES)
            or any(file_digest(path/name)!=manifest['files'][name] for name in FILES)):
        raise ValueError('Candidate integrity mismatch')
    metadata=read_json(path/'metadata.json');probe=read_json(path/'probe.json')
    if metadata.get('task_sha256')!=task_digest(task) or metadata.get('purpose')!='engineering_smoke':raise ValueError('Candidate binding mismatch')
    model=XGBClassifier();model.load_model(path/'model.json')
    names=metadata['feature_names']
    if (not names or len(set(names))!=len(names) or model.get_booster().feature_names!=names
            or model.get_booster().attr('training_task_hash')!=task_digest(task)):
        raise ValueError('Candidate feature metadata mismatch')
    values=pd.DataFrame(probe['values'],columns=names)
    if not 1<=len(values)<=16 or not np.isfinite(values.to_numpy(dtype=float)).all():raise ValueError('Invalid reload probe')
    expected=np.asarray(probe['predictions'],dtype=float)
    if expected.shape!=(len(values),) or not np.isfinite(expected).all() or not np.array_equal(model.predict(values),expected):
        raise ValueError('Candidate reload mismatch')
    metrics=metadata['metrics']
    if not metrics or not all(type(v) in (float,int) and np.isfinite(v) for v in metrics.values()):raise ValueError('Invalid candidate metrics')
    return dict(task_id=task['id'],purpose='engineering_smoke',verified=True,
                manifest_sha256=file_digest(path/'manifest.json'),files=manifest['files'],metrics=metrics)
