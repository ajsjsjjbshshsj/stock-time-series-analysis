"""Owned-child feature staging only. Never activates input or creates a model."""
import json
from sqlalchemy import text
from tasks.artifacts import task_path,task_digest,read_json,write_json,file_digest,_is_link
from .contracts import validate_summary


def inventory(folder):
    files={}
    for path in sorted(folder.rglob('*')):
        if _is_link(path):raise ValueError('Unsafe feature artifact')
        if path.is_file() and path.name not in ('manifest.json','complete.json'):
            files[path.relative_to(folder).as_posix()]=file_digest(path)
        elif path.is_file() and path.parent!=folder:
            # The source manifest is evidence too, unlike the top-level seal.
            files[path.relative_to(folder).as_posix()]=file_digest(path)
        if len(files)>12000:raise ValueError('Feature artifact file count exceeded')
    return files


def verify_build(task,root,engine,*,hash_files=True,states=('BUILDING',),require_inactive=True):
    folder=task_path(root,task['id'])
    manifest=read_json(folder/'manifest.json',max_bytes=4*1024*1024)
    if (set(manifest)!={'schema_version','purpose','task_sha256','files'} or manifest['schema_version']!=1
            or manifest['purpose']!='feature_build' or manifest['task_sha256']!=task_digest(task)
            or not isinstance(manifest['files'],dict) or hash_files and manifest['files']!=inventory(folder)):
        raise ValueError('Feature seal changed')
    sha=file_digest(folder/'manifest.json')
    if read_json(folder/'complete.json')!=dict(manifest_sha256=sha):raise ValueError('Incomplete feature build')
    report=read_json(folder/'report.json',max_bytes=4*1024*1024)
    source=read_json(folder/'source/manifest.json',max_bytes=4*1024*1024)
    for name in ('report.json','source/manifest.json'):
        if file_digest(folder/name)!=manifest['files'].get(name):raise ValueError('Feature metadata hash changed')
    if report.get('build_id')!=task['id'] or report.get('published') is not False:
        raise ValueError('Feature report identity changed')
    for field in ('source_sha256','source_start','source_cutoff','row_count','stock_count'):
        if report[field]!=source[field]:raise ValueError('Feature source report changed')
    with engine.connect() as c:
        stored=c.execute(text('SELECT build_id,state,active_slot,source_start,source_cutoff,source_sha256,output_sha256,row_count,stock_count,source_manifest FROM stock_feature_build WHERE build_id=:id'),dict(id=task['id'])).mappings().one_or_none()
    if stored is None or stored['state'] not in states or require_inactive and stored['active_slot'] is not None:
        raise ValueError('Staging feature build required')
    for key in ('source_start','source_cutoff','source_sha256','output_sha256','row_count','stock_count'):
        actual=str(stored[key]) if key in ('source_start','source_cutoff') else stored[key]
        if actual!=report[key]:raise ValueError('Persisted feature report changed')
    saved=stored['source_manifest'];saved=json.loads(saved) if isinstance(saved,str) else saved
    if saved!=source:raise ValueError('Persisted feature source changed')
    return validate_summary(dict(task_id=task['id'],purpose='feature_build',verified=True,manifest_sha256=sha,
        build_id=task['id'],**{k:report[k] for k in ('source_sha256','output_sha256','row_count','stock_count','source_start','source_cutoff')},
        activated=False,model_ready=False),task)


def feature_progress(emit,total):
    # The protocol/DB cap is2,000 events. A global source has5,000+stocks;
    # batch progress while retaining first/last and ample terminal-event space.
    def update(value):
        completed=value['stocks']
        if completed==1 or completed%50==0 or completed==total:
            emit('progress',phase='INPUT',completed=completed,total=total)
    return update


def run_build(task,root,db,emit):
    from config.logging_config import get_logger
    from .source import FeatureSource
    from tasks import resources
    from tasks.market_profile import bounded_profile
    source=FeatureSource(db.engine);source.verify(task)
    folder=task_path(root,task['id']);resources.preflight(folder,bounded_profile())
    folder.mkdir(parents=True,exist_ok=False)
    from data_processor.feature_store_builder import snapshot_source,compute_build
    from data_processor.build_timings import BuildTimings
    timings=BuildTimings()
    emit('stage',stage='INPUT')
    with timings.measure('source_snapshot'):
        manifest=snapshot_source(db,folder/'source',task['binding']['source_start'],task['binding']['source_cutoff'])
    report=compute_build(db,manifest,task['id'],directory=folder/'source',
        on_progress=feature_progress(emit,manifest['stock_count']),timings=timings)
    emit('stage',stage='VERIFY');source.verify(task)
    write_json(folder/'report.json',report)
    with timings.measure('artifact_inventory'):
        files=inventory(folder)
    # Freeze completed phases before sealing; never rewrite a sealed report
    # to include the time spent verifying its own digest.
    write_json(folder/'performance.json',timings.snapshot())
    files['performance.json']=file_digest(folder/'performance.json')
    write_json(folder/'manifest.json',dict(schema_version=1,purpose='feature_build',task_sha256=task_digest(task),files=files))
    write_json(folder/'complete.json',dict(manifest_sha256=file_digest(folder/'manifest.json')))
    with timings.measure('artifact_verification'):
        proof=verify_build(task,root,db.engine)
    get_logger(__name__).info('Feature build timings: %s',timings.snapshot())
    emit('stage',stage='SAVE');return proof
