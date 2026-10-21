"""Hash-only artifact transport. Native model reload belongs to the worker."""
from pathlib import Path
from tasks.artifacts import task_path,_is_link,read_json,write_json,file_digest
from tasks.contracts import identity
from .contracts import validate_summary


class ArtifactStore:
    def __init__(self,root):self.root=Path(root).absolute()

    def path(self,id):return task_path(self.root,id)

    def inventory(self,folder):
        if any(_is_link(p) for p in (folder,*folder.parents)):raise ValueError('Unsafe workflow root')
        files={}
        for path in sorted(folder.rglob('*')):
            if _is_link(path):raise ValueError('Unsafe workflow file')
            if path.is_file():
                name=path.relative_to(folder).as_posix()
                if name not in ('manifest.json','complete.json'):files[name]=file_digest(path)
            if len(files)>2048:raise ValueError('Workflow file count exceeded')
        return files

    def seal(self,id,metadata):
        folder=self.path(id)
        if any((folder/name).exists() for name in ('metadata.json','manifest.json','complete.json')):
            raise FileExistsError('Workflow seal already exists')
        if not folder.is_dir() or not self.inventory(folder):raise ValueError('Empty workflow artifact')
        proof=dict(task_id=identity(id),purpose='model_workflow',verified=True,manifest_sha256='0'*64,index=metadata['index'])
        validate_summary(proof)
        if metadata.get('schema_version')!=1 or not isinstance(metadata.get('task_sha256'),str) or len(metadata['task_sha256'])!=64:
            raise ValueError('Workflow task binding missing')
        write_json(folder/'metadata.json',metadata)
        write_json(folder/'manifest.json',dict(schema_version=1,purpose='model_workflow',task_id=id,files=self.inventory(folder)))
        write_json(folder/'complete.json',dict(manifest_sha256=file_digest(folder/'manifest.json')))
        return self.verify(id)

    def verify(self,id,*,task_sha256=None):
        folder=self.path(id);manifest=read_json(folder/'manifest.json',max_bytes=1024*1024)
        if manifest!=dict(schema_version=1,purpose='model_workflow',task_id=id,files=self.inventory(folder)):
            raise ValueError('Workflow manifest changed')
        sha=file_digest(folder/'manifest.json')
        if read_json(folder/'complete.json')!=dict(manifest_sha256=sha):raise ValueError('Incomplete workflow')
        metadata=read_json(folder/'metadata.json',max_bytes=8*1024*1024)
        if metadata.get('schema_version')!=1 or task_sha256 is not None and metadata.get('task_sha256')!=task_sha256:
            raise ValueError('Workflow binding mismatch')
        return validate_summary(dict(task_id=id,purpose='model_workflow',verified=True,manifest_sha256=sha,index=metadata['index']))
