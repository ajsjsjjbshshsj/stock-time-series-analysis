"""Feature computational receipts, separate from READY/publication authority."""
from pathlib import Path
import os
from uuid import uuid4
from tasks.artifacts import task_path,task_digest,read_json,write_json,file_digest
from tasks.resumption import parent_task,semantic_identity
from tasks.reuse import ContentStore,safe_path
from tasks.contracts import digest
from data_processor.feature_store_contract import validate_stock_code


def cache(root):return ContentStore(Path(root).absolute().parent/'reusable_inputs/features',max_files=2,max_file_bytes=64*1024**2)


def initialize(task,folder):
    receipts=safe_path(folder/'feature_receipts');receipts.mkdir(exist_ok=False)
    write_json(receipts/'identity.json',dict(schema_version=1,task_sha256=task_digest(task),semantic_sha256=semantic_identity(task),
        source_manifest_sha256=file_digest(folder/'source/manifest.json')))
    def commit(value):
        final=safe_path(receipts/(validate_stock_code(value['ts_code'])+'.json'))
        if final.exists():raise FileExistsError('Feature receipt already committed')
        pending=receipts/('.pending-'+uuid4().hex+'.tmp')
        write_json(pending,value);os.rename(pending,final)
    return commit


def inspect(task,root):
    folder=task_path(root,task['id']);receipts=safe_path(folder/'feature_receipts')
    header=read_json(receipts/'identity.json')
    if header!=dict(schema_version=1,task_sha256=task_digest(task),semantic_sha256=semantic_identity(task),source_manifest_sha256=file_digest(folder/'source/manifest.json')):
        raise ValueError('Feature receipt identity changed')
    source=read_json(folder/'source/manifest.json',max_bytes=4*1024**2);shards={r['ts_code']:r for r in source['shards']}
    files=sorted(p for p in receipts.glob('*.json') if p.name!='identity.json')
    if not 1<=len(files)<=10000:raise ValueError('No bounded feature receipts')
    fingerprints=[];store=cache(root)
    for path in files:
        safe_path(path);value=read_json(path);code=validate_stock_code(value['ts_code'])
        if path.name!=code+'.json' or code not in shards or set(value)!={'ts_code','cache_identity','manifest_sha256'}:
            raise ValueError('Invalid feature receipt member')
        key=value['cache_identity'];shard=shards[code]
        if (key.get('kind')!='raw_feature_stock_v1' or key.get('ts_code')!=code or key.get('source_sha256')!=shard['source_sha256']
                or key.get('row_count')!=shard['row_count'] or key.get('code_sha256')!=task['binding']['feature_algorithm_sha256']):
            raise ValueError('Feature receipt source/algorithm changed')
        cached=store.get(key)
        if cached is None or file_digest(cached[0]/'manifest.json')!=value['manifest_sha256']:raise ValueError('Feature reusable input changed')
        fingerprints.append(file_digest(path))
    return dict(fingerprint=digest(dict(header=header,receipts=fingerprints)),completed_stocks=len(files),source_sha256=source['source_sha256'])


def bind_resume(source,request,binding):
    from workflows.source import artifact_root
    with source.engine.connect() as connection:parent=parent_task(connection,request['resume_task_id'])
    if parent['request']['kind']!='feature_build' or semantic_identity(dict(request=request,binding=binding))!=semantic_identity(parent):
        raise ValueError('Feature resume input changed')
    try:proof=inspect(parent,artifact_root())
    except FileNotFoundError:raise ValueError('No new-format feature checkpoint') from None
    return dict(task_id=parent['id'],checkpoint_sha256=proof['fingerprint'],completed_stocks=proof['completed_stocks'],source_sha256=proof['source_sha256'])
