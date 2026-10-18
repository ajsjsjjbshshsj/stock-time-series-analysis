"""One bounded full-market engineering run; never updates a published model."""
import threading
import time
from .contracts import normalize_request
from .artifacts import task_path,task_digest,write_json,file_digest
from .market_profile import bounded_profile
from . import resources


def run_lightgbm(task,root,source,emit):
    request=normalize_request(task['request'])
    if request['kind']!='lightgbm_all_market':raise ValueError('Market task required')
    path=task_path(root,task['id'])
    if path.exists():raise ValueError('Existing candidate directory')
    work=task_path(path.parent.parent/'work',task['id'])
    if work.exists():raise ValueError('Existing matrix directory')
    profile=bounded_profile();emit('stage',stage='PREFLIGHT');resources.preflight(work,profile)
    started=time.monotonic();stop=threading.Event();peak=[resources.local_rss()]
    def sample():
        while not stop.wait(.2):
            try:peak[0]=max(peak[0],resources.local_rss())
            except Exception:pass  # Worker independently enforces owned tree limits.
    sampler=threading.Thread(target=sample,daemon=True);sampler.start()
    try:
        from .disk_matrix import build_market_matrix,DiskFeatureSequence
        from analysis.disk_probe import disk_probe
        from analysis.disk_ranking import train_ranking_matrix
        from .lightgbm_artifacts import FILES,verify_lightgbm
        emit('stage',stage='INPUT')
        from .market_samples import MarketInputError
        try:bundle=build_market_matrix(task,source.iter_stocks(task),work,emit)
        except MarketInputError as error:
            write_json(work/'input_failure.json',error.diagnostic)
            raise
        emit('stage',stage='PROBE');names,probe_record=disk_probe(bundle,bundle.feature_names,profile,emit)
        emit('stage',stage='TRAIN');model,metadata,metrics=train_ranking_matrix(bundle,names,profile,emit)
        if hasattr(source,'_verify_binding'):source._verify_binding(task)
        emit('stage',stage='VERIFY')
        columns=[bundle.feature_names.index(name) for name in names]
        values=DiskFeatureSequence(bundle.features,bundle.splits['test'],columns)[:min(16,len(bundle.splits['test']))]
        predictions=model.predict(values,num_threads=2)
        peak[0]=max(peak[0],resources.local_rss());elapsed=time.monotonic()-started
        resources.check_time(elapsed,profile)
        if peak[0]>profile['max_tree_rss_bytes']:
            from .contracts import TaskFailure
            raise TaskFailure('RESOURCE_LIMIT')
        metadata.update(schema_version=1,kind='lightgbm_all_market',task_sha256=task_digest(task),source=task['binding'],metrics=metrics,
            probe=probe_record,resources=dict(peak_rss_bytes=peak[0],elapsed_seconds=elapsed))
        path.parent.mkdir(parents=True,exist_ok=True);path.mkdir()
        model.save_model(str(path/'model.txt'))
        write_json(path/'metadata.json',metadata);write_json(path/'coverage.json',bundle.coverage)
        write_json(path/'probe.json',dict(values=values.tolist(),predictions=predictions.tolist()))
        write_json(path/'manifest.json',dict(schema_version=1,kind='lightgbm_all_market',task_sha256=task_digest(task),
            files={name:file_digest(path/name) for name in FILES}))
        candidate=verify_lightgbm(task,root);emit('stage',stage='SAVE');return candidate
    finally:stop.set();sampler.join(timeout=2)
