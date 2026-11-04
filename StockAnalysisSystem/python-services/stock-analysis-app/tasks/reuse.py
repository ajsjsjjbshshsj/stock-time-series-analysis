"""Immutable, content-addressed computational inputs. Never task/result authority."""
import os
from pathlib import Path
import re
from uuid import uuid4
from .artifacts import _is_link,file_digest,read_json,write_json
from .contracts import digest


def safe_path(path):
    path=Path(path).absolute()
    if any(_is_link(p) for p in (path,*path.parents)):raise ValueError('Unsafe reusable input path')
    return path


def bounded_hashes(paths,digest_function=file_digest):
    """Always hash actual bytes; bounded I/O parallelism is not an mtime cache."""
    if len(paths)<4:return [digest_function(path) for path in paths]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='artifact-hash') as executor:
        result=list(executor.map(digest_function,paths))
    for path in paths:safe_path(path)
    return result


class ContentStore:
    def __init__(self,root,*,max_files=32,max_file_bytes=16*1024**3):
        self.root=safe_path(root);self.max_files=max_files;self.max_file_bytes=max_file_bytes
        protected=Path(__file__).resolve().parents[1]/'models'
        if self.root==protected or self.root.is_relative_to(protected) or protected.is_relative_to(self.root):
            raise ValueError('Reusable inputs overlap protected models')

    def _inventory(self,folder):
        safe_path(folder);files={}
        for path in sorted(folder.iterdir()):
            safe_path(path)
            if not path.is_file() or not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',path.name):raise ValueError('Unsafe reusable input member')
            if path.name=='manifest.json':continue
            size=path.stat().st_size
            if size>self.max_file_bytes:raise ValueError('Reusable input file bound')
            files[path.name]=dict(size=size,sha256=file_digest(path))
            if len(files)>self.max_files:raise ValueError('Reusable input file count bound')
        return files

    def get(self,identity):
        key=digest(identity);folder=safe_path(self.root/key)
        if not folder.exists():return None
        manifest=read_json(folder/'manifest.json',max_bytes=1024*1024)
        if (set(manifest)!={'schema_version','key','identity','metadata','files'} or manifest['schema_version']!=1
                or manifest['key']!=key or manifest['identity']!=identity or not isinstance(manifest['metadata'],dict)
                or manifest['files']!=self._inventory(folder) or not manifest['files']):
            raise ValueError('Reusable input integrity changed')
        return folder,manifest['metadata']

    def publish(self,identity,builder):
        existing=self.get(identity)
        if existing is not None:return existing
        safe_path(self.root).mkdir(parents=True,exist_ok=True)
        temporary=safe_path(self.root/('.pending-'+uuid4().hex));temporary.mkdir(exist_ok=False)
        metadata=builder(temporary)
        if not isinstance(metadata,dict):raise ValueError('Reusable input metadata required')
        files=self._inventory(temporary)
        if not files:raise ValueError('Empty reusable input')
        # Flush every completed member before publishing the directory.
        for name in files:
            with safe_path(temporary/name).open('r+b') as stream:stream.flush();os.fsync(stream.fileno())
        key=digest(identity)
        write_json(temporary/'manifest.json',dict(schema_version=1,key=key,identity=identity,metadata=metadata,files=files))
        # A producer cannot publish an entry that this store's reader refuses.
        read_json(temporary/'manifest.json',max_bytes=1024*1024)
        target=safe_path(self.root/key)
        try:os.rename(temporary,target)  # Existing nonempty immutable entries are never replaced.
        except OSError:
            if not target.exists():raise
            # Another writer may have published the same identity; inspect it,
            # never delete/overwrite it or guess that the pending directory is complete.
        return self.get(identity)


def read_parquet(path,*,max_rows,max_columns,max_uncompressed=64*1024**2):
    import pyarrow.parquet as pq
    path=safe_path(path)
    if path.stat().st_size>max_uncompressed:raise ValueError('Reusable parquet compressed bound')
    dictionaries=['ts_code'] if 'ts_code' in pq.read_schema(path).names else None
    with pq.ParquetFile(path,read_dictionary=dictionaries) as parquet:
        return _read_flat_parquet(parquet,max_rows,max_columns,max_uncompressed)


def _read_flat_parquet(parquet,max_rows,max_columns,max_uncompressed):
    import pyarrow as pa
    import pyarrow.compute as pc
    meta=parquet.metadata
    if meta.num_rows>max_rows or meta.num_columns>max_columns or meta.num_row_groups>256:
        raise ValueError('Reusable parquet shape bound')
    size=sum(meta.row_group(i).column(j).total_uncompressed_size for i in range(meta.num_row_groups) for j in range(meta.num_columns))
    if not 0<=size<=max_uncompressed:raise ValueError('Reusable parquet decoded byte bound')
    schema=parquet.schema_arrow;estimated_width=0
    if len(schema)!=meta.num_columns:raise ValueError('Nested reusable parquet is forbidden')
    for field in schema:
        kind=field.type
        if not meta.num_rows and pa.types.is_null(kind):continue
        if field.name=='ts_code':
            if pa.types.is_dictionary(kind):kind=kind.value_type
            if not pa.types.is_string(kind):raise ValueError('Invalid stock physical type')
            estimated_width+=128
        elif field.name in ('trade_date','label_target_date'):
            if not (pa.types.is_date(kind) or pa.types.is_timestamp(kind)):raise ValueError('Invalid date physical type')
            estimated_width+=16
        else:
            if not (pa.types.is_floating(kind) or pa.types.is_integer(kind) or pa.types.is_null(kind)):
                raise ValueError('Only fixed-width numeric feature payloads are accepted')
            estimated_width+=16
    if meta.num_rows*estimated_width>max_uncompressed:raise ValueError('Reusable parquet decoded allocation bound')
    batches=[]
    for batch in parquet.iter_batches(batch_size=256):
        if 'ts_code' in schema.names:
            values=batch.column(schema.get_field_index('ts_code'))
            # Keep dictionary encoding during inspection; a repeated long
            # string must not expand into a pandas object column first.
            if pa.types.is_dictionary(values.type):values=values.dictionary
            if values.null_count or not pc.all(pc.match_substring_regex(values,r'^\d{6}\.(SH|SZ)$')).as_py():
                raise ValueError('Invalid reusable stock values')
        batches.append(batch)
    return pa.Table.from_batches(batches,schema=schema).to_pandas()
