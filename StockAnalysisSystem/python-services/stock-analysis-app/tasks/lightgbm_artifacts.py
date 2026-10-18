"""Exact native LightGBM candidate format, separate from XGBoost and releases."""
import re
import numpy as np
from .artifacts import task_path,task_digest,read_json,file_digest,_is_link
from .contracts import encoded,digest
from .market_profile import bounded_profile,parameters

FILES=('model.txt','metadata.json','probe.json','coverage.json')


def validate_summary(value,task):
    expected={'task_id','kind','purpose','verified','published','manifest_sha256','files','metrics','coverage','resources'}
    if (not isinstance(value,dict) or set(value)!=expected or value['task_id']!=task['id']
            or value['kind']!='lightgbm_all_market' or value['purpose']!='engineering_smoke'
            or value['verified'] is not True or value['published'] is not False or len(encoded(value).encode())>8192):
        raise ValueError('Invalid market candidate summary')
    hashes=[value['manifest_sha256'],*value['files'].values()]
    if set(value['files'])!=set(FILES) or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes):
        raise ValueError('Invalid candidate hashes')
    coverage=value['coverage'];binding=task['binding']
    if (coverage['pool_count']!=binding['pool_count'] or coverage['pool_identity']!=binding['pool_identity']
            or set(coverage)!= {'pool_count','pool_identity','eligible_rows','purged_rows','zero_eligible_stocks','splits'}
            or any(type(coverage[k]) is not int or coverage[k]<0 for k in ('eligible_rows','purged_rows','zero_eligible_stocks'))
            or not coverage['eligible_rows'] or coverage['zero_eligible_stocks']>coverage['pool_count']
            or set(coverage['splits'])!={'train','val','test'}
            or any(type(v) is not int or v<=0 for v in coverage['splits'].values())
            or sum(coverage['splits'].values())+coverage['purged_rows']!=coverage['eligible_rows']):
        raise ValueError('Invalid candidate coverage')
    metrics=value['metrics'];resources=value['resources'];profile=bounded_profile()
    if (set(metrics)!={'RMSE','Direction_Accuracy'} or any(type(v) not in (int,float) or not np.isfinite(v) for v in metrics.values())
            or metrics['RMSE']<0 or not 0<=metrics['Direction_Accuracy']<=1
            or set(resources)!={'peak_rss_bytes','elapsed_seconds'}
            or type(resources['peak_rss_bytes']) is not int or not 0<resources['peak_rss_bytes']<=profile['max_tree_rss_bytes']
            or type(resources['elapsed_seconds']) not in (int,float)
            or not 0<=resources['elapsed_seconds']<=profile['max_seconds']):raise ValueError('Invalid metrics or resources')
    return value


def verify_lightgbm(task,root):
    import lightgbm as lgb
    from data_processor.feature_store_contract import FEATURE_COLUMNS
    path=task_path(root,task['id'])
    if not path.is_dir() or {p.name for p in path.iterdir()}!={*FILES,'manifest.json'}:raise ValueError('Incomplete market candidate')
    if any(_is_link(path/name) for name in (*FILES,'manifest.json')):raise ValueError('Unsafe candidate file')
    manifest=read_json(path/'manifest.json');sha=task_digest(task)
    if (manifest.get('schema_version')!=1 or manifest.get('kind')!='lightgbm_all_market' or manifest.get('task_sha256')!=sha
            or set(manifest.get('files',{}))!=set(FILES)
            or any(file_digest(path/name)!=manifest['files'][name] for name in FILES)):
        raise ValueError('Market candidate integrity mismatch')
    metadata=read_json(path/'metadata.json');probe=read_json(path/'probe.json')
    coverage=read_json(path/'coverage.json',max_bytes=16*1024*1024)
    names=metadata['feature_names'];profile=bounded_profile()
    if (metadata.get('task_sha256')!=sha or metadata.get('source')!=task['binding']
            or metadata.get('purpose')!='engineering_smoke' or metadata.get('published') is not False
            or metadata.get('params')!=parameters(profile,'regression') or metadata.get('horizon')!=5
            or not names or names!=[n for n in FEATURE_COLUMNS if n in names]
            or metadata['probe']['selected_features']!=names or not 0<metadata['rounds']<=20):
        raise ValueError('Market candidate metadata mismatch')
    codes=task['binding']['stock_codes']
    if (coverage['pool_count']!=len(codes) or coverage['pool_identity']!=digest(codes)
            or set(coverage['stocks'])!=set(codes)
            or coverage['eligible_rows']!=sum(r['eligible_rows'] for r in coverage['stocks'].values())):
        raise ValueError('Candidate pool mismatch')
    if (path/'model.txt').stat().st_size>8*1024*1024:raise ValueError('Candidate model bound exceeded')
    model=lgb.Booster(model_file=str(path/'model.txt'))
    if model.feature_name()!=names or model.current_iteration()!=metadata['rounds']:raise ValueError('Native feature mismatch')
    values=np.asarray(probe['values'],dtype=float);expected=np.asarray(probe['predictions'],dtype=float)
    if (values.ndim!=2 or not 1<=len(values)<=16 or values.shape[1]!=len(names) or not np.isfinite(values).all()
            or expected.shape!=(len(values),) or not np.isfinite(expected).all()
            or not np.allclose(model.predict(values,num_threads=2),expected,rtol=1e-12,atol=1e-12)):
        raise ValueError('Native reload probe mismatch')
    summary=dict(task_id=task['id'],kind='lightgbm_all_market',purpose='engineering_smoke',verified=True,published=False,
        manifest_sha256=file_digest(path/'manifest.json'),files=manifest['files'],metrics=metadata['metrics'],resources=metadata['resources'],
        coverage=dict(pool_count=len(codes),pool_identity=coverage['pool_identity'],eligible_rows=coverage['eligible_rows'],
            purged_rows=coverage['purged_rows'],zero_eligible_stocks=sum(r['eligible_rows']==0 for r in coverage['stocks'].values()),
            splits={k:v['count'] for k,v in metadata['splits'].items()}))
    return validate_summary(summary,task)
