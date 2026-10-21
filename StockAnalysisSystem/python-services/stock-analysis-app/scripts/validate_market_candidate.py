"""Explicit local validation-only research. No test evaluation or publication."""
import argparse,json,os,sys,time,uuid
from pathlib import Path
APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))


def git_metadata_paths(checkout):
    """Resolve ordinary and linked-worktree admin paths without invoking Git."""
    marker=Path(checkout)/'.git'
    if not marker.exists():return []
    if marker.is_dir():return [marker.resolve()]
    if marker.stat().st_size>4096:raise ValueError('Invalid Git marker')
    value=marker.read_text(encoding='utf-8').strip()
    if not value.startswith('gitdir: '):raise ValueError('Invalid Git marker')
    admin=(marker.parent/value[8:]).resolve();common=admin/'commondir'
    paths=[marker.resolve(),admin]
    if common.exists():
        if common.stat().st_size>4096:raise ValueError('Invalid Git common directory')
        paths.append((admin/common.read_text(encoding='utf-8').strip()).resolve())
    return paths


def output_directory(project,output_root,protected,experiment_id):
    from tasks.artifacts import task_path
    root=Path(output_root)
    if not root.is_absolute():root=Path(project)/root
    if root.resolve()==Path(project).resolve():raise ValueError('Research output overlaps project root')
    folder=task_path(root,experiment_id)
    for item in protected:
        a,b=folder.resolve(),Path(item).resolve()
        if a.is_relative_to(b) or b.is_relative_to(a):raise ValueError('Research output overlaps protected inputs')
    return folder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-id',required=True)
    parser.add_argument('--project-root',type=Path,default=APP.parents[1])
    parser.add_argument('--output-root',type=Path,default=Path('.runtime/training_tasks/validation_experiments'))
    args=parser.parse_args();project=args.project_root.resolve()
    from dotenv import load_dotenv
    load_dotenv(project/'.env',override=True)
    from sqlalchemy import text
    from database.db_connector import DatabaseConnector
    from tasks.artifacts import task_path,read_json,write_json,file_digest
    from tasks.lightgbm_artifacts import verify_lightgbm
    from tasks.market_source import MarketSource
    from tasks.disk_matrix import MatrixBundle
    from tasks.market_profile import bounded_profile
    from tasks import resources
    from analysis.market_evaluation import check_binding
    from analysis.market_validation import run_trials,seal_validation,verify_validation
    sys.path.append(str(APP.parent/'python-collector'))
    from app.runtime_paths import RuntimePaths
    paths=RuntimePaths.from_environment(project,os.environ)
    candidate_root=Path(os.getenv('TRAINING_ARTIFACT_ROOT','.runtime/training_tasks/candidates'))
    if not candidate_root.is_absolute():candidate_root=project/candidate_root
    work_root=candidate_root.parent/'work'
    candidate=task_path(candidate_root,args.task_id);work=task_path(work_root,args.task_id)
    protected=[candidate_root,work_root,paths.data_root,APP/'models',project/'python-services/stock-analysis-app/models',
               project/'.git',project.parent/'.git']
    protected+=git_metadata_paths(APP.parents[2])+git_metadata_paths(project.parent)
    folder=output_directory(project,args.output_root,protected,uuid.uuid4().hex)
    profile=bounded_profile();resources.preflight(folder,profile);started=time.monotonic();peak=[0]
    def emit(**value):print(json.dumps(value),flush=True)
    def guard():
        resources.check_time(time.monotonic()-started,profile)
        peak[0]=max(peak[0],resources.local_rss())
        if peak[0]>profile['max_tree_rss_bytes'] or resources._disk(folder)<profile['min_runtime_disk_bytes']:
            raise ValueError('Research resource limit')
    emit(stage='VERIFY_INPUT',task_id=args.task_id,test_evaluated=False,published=False)
    with DatabaseConnector(diagnostic=True) as db:
        with db.engine.connect() as connection:
            row=connection.execute(text('SELECT id,request,binding,state FROM analysis_training_task WHERE id=:id'),
                dict(id=args.task_id)).mappings().one()
            active=connection.execute(text("SELECT COUNT(*) FROM analysis_training_task WHERE state IN ('QUEUED','RUNNING','CANCEL_REQUESTED')")).scalar_one()
        if row['state']!='SUCCEEDED' or active:raise ValueError('Successful candidate and idle training queue required')
        task=dict(id=row['id'],request=json.loads(row['request']),binding=json.loads(row['binding']))
        # Existing native reload proof is integrity-only; it does not fit or
        # evaluate new models on test data, nor recalculate historical metrics.
        original=verify_lightgbm(task,candidate_root);meta=read_json(candidate/'metadata.json')
        calendar=Path(os.getenv('TRAINING_CALENDAR_FILE','.runtime/training_tasks/calendar.json'))
        if not calendar.is_absolute():calendar=project/calendar
        source=MarketSource(db.engine,calendar,evidence_root=paths.data_root)
        current=source.bind(task['request']);check_binding(task['binding'],current)
        bundle=MatrixBundle.open(work)
        matrix_sha=file_digest(work/'matrix.json')
        if (bundle.split_metadata!=meta['splits'] or file_digest(work/'coverage.json')!=file_digest(candidate/'coverage.json')
                or bundle.coverage['pool_identity']!=current['pool_identity'] or bundle.coverage['pool_count']!=current['pool_count']):
            raise ValueError('Original matrix/candidate lineage mismatch')
        code_paths=[APP/'analysis/market_validation.py',APP/'tasks/disk_matrix.py',APP/'tasks/market_profile.py',
                    APP/'tasks/lightgbm_artifacts.py',Path(__file__)]
        code_hashes={p.name:file_digest(p) for p in code_paths}
        emit(stage='BOUND',output=str(folder),pool_count=current['pool_count'],features=len(meta['feature_names']))
        report=run_trials(bundle,meta['feature_names'],folder,guard,emit)
        write_json(folder/'binding.json',dict(original_task_id=task['id'],original_candidate_manifest_sha256=original['manifest_sha256'],
            original_probe_sha256=file_digest(candidate/'probe.json'),original_train_only_probe=meta['probe'],
            matrix_manifest_sha256=matrix_sha,training_binding=task['binding'],research_binding=current,code_files=code_hashes,
            test_evaluated=False,published=False,resources=dict(peak_rss_bytes=peak[0],elapsed_seconds=time.monotonic()-started)))
        # Recheck immutable inputs even if an IDE/worker changed files meanwhile.
        if source.bind(task['request'])!=current or verify_lightgbm(task,candidate_root)!=original:
            raise ValueError('Research source/candidate changed')
        MatrixBundle.open(work)
        if file_digest(work/'matrix.json')!=matrix_sha or any(file_digest(p)!=code_hashes[p.name] for p in code_paths):
            raise ValueError('Research matrix/code changed')
        guard();seal_validation(folder,report);verify_validation(folder)
        emit(stage='DONE',output=str(folder),status=report['status'],best_trial=report['best_trial'],
            qualified=report['qualified'],test_evaluated=False,published=False)


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(130)
    except Exception as exc:
        print(json.dumps(dict(status='VALIDATION_FAILED',error_type=type(exc).__name__,published=False)),file=sys.stderr)
        raise SystemExit(1)
