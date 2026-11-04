"""Committed scoring prefixes, never a result/publication or a process recovery authority."""
from copy import deepcopy
from datetime import date
import os
from pathlib import Path
import re
from uuid import uuid4
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from tasks.artifacts import _is_link,read_json,write_json,file_digest,task_digest
from tasks.contracts import digest,identity
from .contracts import normalize_operation

CHUNK_SIZE=100
MAX_CHUNKS=256
MAX_UNCOMPRESSED_BYTES=64*1024*1024


def semantic_identity(task):
    request=normalize_operation(task['request'])
    if request['operation']!='predict' or request['algorithm']!='lightgbm':raise ValueError('Unsupported checkpoint operation')
    request.pop('resume_task_id',None)
    binding=deepcopy(task['binding']);binding.pop('resume',None)
    codes=binding['data']['stock_codes']
    if (not isinstance(codes,list) or not 1<=len(codes)<=10000 or codes!=sorted(set(codes))
            or any(not isinstance(c,str) or not re.fullmatch(r'[0-9]{6}\.(SH|SZ)',c) for c in codes)
            or binding['pool_count']!=len(codes)):
        raise ValueError('Invalid checkpoint pool')
    return digest(dict(request=request,binding=binding))


def safe(path):
    path=Path(path).absolute()
    if any(_is_link(p) for p in (path,*path.parents)):raise ValueError('Unsafe checkpoint path')
    return path


def validate_chunk(task,codes,scores,coverage):
    if not isinstance(codes,list) or not 1<=len(codes)<=CHUNK_SIZE or len(set(codes))!=len(codes):
        raise ValueError('Invalid checkpoint chunk members')
    if (list(scores.columns)!=['ts_code','trade_date','score'] or scores.duplicated(['ts_code','trade_date']).any()
            or not set(scores.ts_code)<=set(codes) or not np.isfinite(scores.score.to_numpy(dtype=float)).all()):
        raise ValueError('Invalid checkpoint scores')
    r=task['request']
    for day in set(scores.trade_date):
        if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day or not r['start_date']<=day<=r['end_date']:
            raise ValueError('Invalid checkpoint score dates')
    if not isinstance(coverage,list) or len(coverage)!=len(codes):raise ValueError('Incomplete checkpoint coverage')
    for code,report in zip(codes,coverage):
        if (not isinstance(report,dict) or report.get('ts_code')!=code or type(report.get('eligible_rows')) is not int
                or report['eligible_rows']!=int((scores.ts_code==code).sum()) or not isinstance(report.get('reasons'),dict)
                or any(type(n) is not int or n<0 for n in report['reasons'].values())):
            raise ValueError('Invalid checkpoint eligibility evidence')
        if report['eligible_rows']==0 and not report['reasons']:raise ValueError('Unexplained empty stock')
    digest(coverage)  # Reject nonfinite/unserializable eligibility evidence before committing scores.


class Checkpoint:
    def __init__(self,task,directory,header,chunks=None):
        self.task=task;self.directory=safe(directory);self.header=header;self.chunks=chunks or []
        self.codes=task['binding']['data']['stock_codes'];self.completed_codes=[c for chunk in self.chunks for c in chunk['codes']]

    @classmethod
    def create(cls,task,directory):
        directory=safe(directory);directory.mkdir(parents=True,exist_ok=False)
        header=dict(schema_version=1,task_id=identity(task['id']),task_sha256=task_digest(task),semantic_sha256=semantic_identity(task))
        write_json(directory/'identity.json',header)
        return cls(task,directory,header)

    @classmethod
    def inspect(cls,task,directory):
        directory=safe(directory);header=read_json(directory/'identity.json')
        expected=dict(schema_version=1,task_id=identity(task['id']),task_sha256=task_digest(task),semantic_sha256=semantic_identity(task))
        if header!=expected:raise ValueError('Checkpoint identity changed')
        chunks=[];completed=[];hashes=[];codes=task['binding']['data']['stock_codes']
        files=sorted(directory.glob('[0-9]*.json'))
        if len(files)>MAX_CHUNKS:raise ValueError('Checkpoint chunk bound exceeded')
        for i,path in enumerate(files):
            if path.name!=f'{i:05d}.json':raise ValueError('Noncontiguous checkpoint commits')
            record=read_json(safe(path),max_bytes=4*1024*1024)
            if (set(record)!={'schema_version','index','previous_sha256','codes','score_rows','scores_sha256','coverage'}
                    or record['schema_version']!=1 or record['index']!=i
                    or record['previous_sha256']!=(hashes[-1] if hashes else digest(header))
                    or not isinstance(record['codes'],list) or not 1<=len(record['codes'])<=CHUNK_SIZE
                    or record['codes']!=codes[len(completed):len(completed)+len(record['codes'])]):
                raise ValueError('Checkpoint commit chain changed')
            days=(date.fromisoformat(task['request']['end_date'])-date.fromisoformat(task['request']['start_date'])).days+1
            if (type(record['score_rows']) is not int or not 0<=record['score_rows']<=len(record['codes'])*days
                    or not isinstance(record['coverage'],list) or len(record['coverage'])!=len(record['codes'])
                    or not isinstance(record['scores_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',record['scores_sha256'])):
                raise ValueError('Checkpoint declared bounds invalid')
            for code,report in zip(record['codes'],record['coverage']):
                if (not isinstance(report,dict) or report.get('ts_code')!=code
                        or type(report.get('eligible_rows')) is not int or not 0<=report['eligible_rows']<=days):
                    raise ValueError('Checkpoint coverage bounds invalid')
            if sum(report['eligible_rows'] for report in record['coverage'])!=record['score_rows']:
                raise ValueError('Checkpoint coverage count differs')
            scores=cls._scores(task,directory,i,record)
            validate_chunk(task,record['codes'],scores,record['coverage'])
            if type(record['score_rows']) is not int or len(scores)!=record['score_rows']:raise ValueError('Checkpoint row count mismatch')
            completed.extend(record['codes']);chunks.append(record);hashes.append(file_digest(path))
        return dict(header=header,chunks=chunks,completed_codes=completed,
            fingerprint=digest(dict(header=header,commits=hashes)))

    @staticmethod
    def _scores(task,directory,index,record):
        path=safe(directory/f'{index:05d}.parquet')
        if path.stat().st_size>64*1024*1024 or file_digest(path)!=record['scores_sha256']:
            raise ValueError('Checkpoint score file changed')
        parquet=pq.ParquetFile(path);meta=parquet.metadata
        days=(date.fromisoformat(task['request']['end_date'])-date.fromisoformat(task['request']['start_date'])).days+1
        if (meta.num_rows!=record['score_rows'] or meta.num_rows>len(record['codes'])*days or meta.num_columns!=3
                or meta.num_row_groups>256 or parquet.schema_arrow.names!=['ts_code','trade_date','score']):
            raise ValueError('Checkpoint score bound exceeded')
        uncompressed=sum(meta.row_group(i).column(j).total_uncompressed_size
            for i in range(meta.num_row_groups) for j in range(meta.num_columns))
        if not 0<=uncompressed<=MAX_UNCOMPRESSED_BYTES:raise ValueError('Checkpoint uncompressed byte bound exceeded')
        return pd.read_parquet(path)

    def commit(self,codes,scores,coverage):
        if len(self.chunks)>=MAX_CHUNKS or codes!=self.codes[len(self.completed_codes):len(self.completed_codes)+len(codes)]:
            raise ValueError('Checkpoint prefix order changed')
        validate_chunk(self.task,codes,scores,coverage)
        index=len(self.chunks);path=self.directory/f'{index:05d}.parquet'
        # Data reaches disk before the exclusive, atomically renamed commit record.
        with safe(path).open('xb') as stream:
            scores=scores.copy();scores.attrs={};scores.to_parquet(stream,index=False);stream.flush();os.fsync(stream.fileno())
        record=dict(schema_version=1,index=index,previous_sha256=file_digest(self.directory/f'{index-1:05d}.json') if index else digest(self.header),
            codes=list(codes),score_rows=len(scores),scores_sha256=file_digest(path),coverage=deepcopy(coverage))
        pending=self.directory/f'.pending-{uuid4().hex}.json';write_json(pending,record)
        committed=safe(self.directory/f'{index:05d}.json')
        if committed.exists():raise FileExistsError('Checkpoint commit exists')
        os.replace(pending,committed)
        self.chunks.append(record);self.completed_codes.extend(codes)

    def copy_prefix(self,parent_task,parent_directory,fingerprint):
        if self.chunks:raise ValueError('Resume target is not empty')
        proof=self.inspect(parent_task,parent_directory)
        if (not proof['completed_codes'] or proof['fingerprint']!=fingerprint
                or proof['header']['semantic_sha256']!=self.header['semantic_sha256']):
            raise ValueError('Resume checkpoint changed or incompatible')
        for i,record in enumerate(proof['chunks']):
            scores=self._scores(parent_task,safe(parent_directory),i,record)
            self.commit(record['codes'],scores,record['coverage'])

    def iter_chunks(self):
        for i,record in enumerate(self.chunks):yield self._scores(self.task,self.directory,i,record),record['coverage']
