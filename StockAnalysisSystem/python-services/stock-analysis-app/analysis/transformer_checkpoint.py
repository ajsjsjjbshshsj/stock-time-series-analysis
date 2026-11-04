"""Epoch-boundary training state. Inspection is torch-free; loading is worker-only."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import random
import re
import pickletools
import zipfile
from pathlib import PurePosixPath
from uuid import uuid4
from tasks.artifacts import read_json,write_json,file_digest
from tasks.contracts import digest
from tasks.reuse import safe_path

MAX_BYTES=512*1024**2


def validate_archive(path):
    """Native uncompressed torch.save only, with pre-load storage/metadata bounds."""
    try:
        with zipfile.ZipFile(path) as archive:
            members=archive.infolist()
            if not 3<=len(members)<=4096:raise ValueError('Checkpoint member bound')
            seen=set();roots=set();total=0;pickle_member=None;required=set()
            for member in members:
                parts=PurePosixPath(member.filename).parts
                if (len(parts)<2 or any(p in ('..','.') for p in parts) or member.filename.startswith(('/','\\'))
                        or '\\' in member.filename or member.filename in seen or member.is_dir()
                        or member.flag_bits&1 or member.compress_type!=zipfile.ZIP_STORED
                        or member.compress_size!=member.file_size or not 0<=member.file_size<=MAX_BYTES):
                    raise ValueError('Invalid native checkpoint archive')
                roots.add(parts[0]);seen.add(member.filename);name='/'.join(parts[1:]);total+=member.file_size
                if name=='data.pkl':
                    if member.file_size>4*1024**2:raise ValueError('Checkpoint metadata bound')
                    pickle_member=member
                elif name.startswith('data/'):
                    if len(parts)!=3 or not parts[2].isdigit():raise ValueError('Invalid native storage member')
                elif name not in ('version','byteorder','.format_version','.storage_alignment','.data/serialization_id'):
                    raise ValueError('Unexpected checkpoint member')
                required.add(name)
            if len(roots)!=1 or total>MAX_BYTES or not {'data.pkl','version','byteorder'}<=required:
                raise ValueError('Checkpoint storage bound')
            for count,_ in enumerate(pickletools.genops(archive.read(pickle_member)),1):
                if count>500000:raise ValueError('Checkpoint object count bound')
    except (zipfile.BadZipFile,EOFError) as error:raise ValueError('Invalid native checkpoint archive') from error


def scaler_state(scaler):
    import numpy as np
    return {name:np.asarray(getattr(scaler,name)).tolist() for name in ('mean_','var_','scale_','n_samples_seen_')}


def data_identity(train,val,full_features,selected_features,mapping,scaler):
    import numpy as np
    sha=hashlib.sha256();sha.update(digest(dict(full=full_features,selected=selected_features,mapping=mapping,scaler=scaler_state(scaler))).encode())
    def add(value):
        if value is None:sha.update(b'null');return
        if isinstance(value,(list,tuple)):
            sha.update(f'list:{len(value)}:'.encode())
            for part in value:add(part)
        else:
            array=np.asarray(value)
            if array.dtype.kind not in 'fiu':raise ValueError('Invalid checkpoint input dtype')
            sha.update(digest(dict(dtype=array.dtype.str,shape=list(array.shape))).encode());sha.update(np.ascontiguousarray(array).tobytes())
    add(train);add(val)
    return sha.hexdigest()


class EpochCheckpoint:
    def __init__(self,directory,identity):self.directory=safe_path(directory);self.identity=identity

    @staticmethod
    def inspect(directory,*,expected_task=None):
        directory=safe_path(directory);files=sorted(directory.glob('epoch-*.json'))
        if not 1<=len(files)<=10000:raise ValueError('No bounded committed epoch checkpoint')
        path=safe_path(files[-1]);record=read_json(path,max_bytes=1024*1024)
        if (set(record)!={'schema_version','epoch','identity','weights_file','weights_sha256','size','extra_files'}
                or record['schema_version']!=1 or type(record['epoch']) is not int or not 1<=record['epoch']<=10000
                or path.name!=f"epoch-{record['epoch']:06d}.json" or record['weights_file']!=f"epoch-{record['epoch']:06d}.pt"
                or not isinstance(record['identity'],dict) or expected_task is not None and record['identity'].get('task')!=expected_task
                or type(record['size']) is not int or not 0<record['size']<=MAX_BYTES
                or not isinstance(record['weights_sha256'],str) or not re.fullmatch('[0-9a-f]{64}',record['weights_sha256'])
                or not isinstance(record['extra_files'],dict) or set(record['extra_files'])-{'probe_selection.json'}):
            raise ValueError('Invalid epoch checkpoint record')
        weights=safe_path(directory/record['weights_file'])
        if weights.stat().st_size!=record['size'] or file_digest(weights)!=record['weights_sha256']:raise ValueError('Epoch checkpoint changed')
        validate_archive(weights)
        for name,sha in record['extra_files'].items():
            if not isinstance(sha,str) or not re.fullmatch('[0-9a-f]{64}',sha) or file_digest(safe_path(directory.parent/name))!=sha:
                raise ValueError('Checkpoint probe evidence changed')
        return dict(directory=directory,record=record,epoch=record['epoch'],fingerprint=digest(dict(record_sha256=file_digest(path),weights_sha256=record['weights_sha256'])))

    def save(self,epoch,model,optimizer,scheduler,stopping,history,best_score,best_epoch,best_state,stopped_early,*,preprocessing=None):
        import torch
        import numpy as np
        if type(epoch) is not int or not 1<=epoch<=10000 or len(history)!=epoch:raise ValueError('Invalid completed epoch')
        self.directory.mkdir(parents=True,exist_ok=True)
        numpy_state=np.random.get_state()
        rng=dict(python=random.getstate(),numpy=[numpy_state[0],numpy_state[1].tolist(),numpy_state[2],numpy_state[3],numpy_state[4]],
            torch=torch.get_rng_state(),cuda=torch.cuda.get_rng_state_all() if self.identity.get('device')=='cuda' else None)
        state=dict(schema_version=1,epoch=epoch,identity=self.identity,model=model.state_dict(),optimizer=optimizer.state_dict(),
            scheduler=scheduler.state_dict(),stopping=dict(best=stopping.best,wait=stopping.wait,patience=stopping.patience,min_delta=stopping.min_delta),
            history=deepcopy(history),best_score=best_score,best_epoch=best_epoch,best_state=best_state,stopped_early=bool(stopped_early),rng=rng,preprocessing=preprocessing)
        def native_scalars(value):
            # Real ranking metrics use NumPy scalars; never expand torch's safe
            # loader allowlist or fall back to arbitrary pickle execution.
            if isinstance(value,np.generic):return value.item()
            if isinstance(value,np.ndarray):return value.tolist()
            if isinstance(value,dict):return {key:native_scalars(part) for key,part in value.items()}
            if isinstance(value,list):return [native_scalars(part) for part in value]
            if isinstance(value,tuple):return tuple(native_scalars(part) for part in value)
            return value
        state=native_scalars(state)
        weights=safe_path(self.directory/f'epoch-{epoch:06d}.pt')
        with weights.open('xb') as stream:torch.save(state,stream);stream.flush();os.fsync(stream.fileno())
        if weights.stat().st_size>MAX_BYTES:raise ValueError('Epoch checkpoint byte bound exceeded')
        extras={}
        probe=safe_path(self.directory.parent/'probe_selection.json')
        if probe.exists():extras[probe.name]=file_digest(probe)
        record=dict(schema_version=1,epoch=epoch,identity=self.identity,weights_file=weights.name,weights_sha256=file_digest(weights),size=weights.stat().st_size,extra_files=extras)
        pending=self.directory/f'.pending-{uuid4().hex}.json';write_json(pending,record)
        final=safe_path(self.directory/f'epoch-{epoch:06d}.json')
        if final.exists():raise FileExistsError('Epoch commit already exists')
        os.replace(pending,final)

    def restore(self,proof,model,optimizer,scheduler,stopping):
        import torch
        import numpy as np
        current=self.inspect(proof['directory'])
        if current['fingerprint']!=proof['fingerprint'] or current['record']['identity']!=self.identity:
            raise ValueError('Epoch checkpoint context changed')
        state=torch.load(safe_path(current['directory']/current['record']['weights_file']),map_location='cpu',weights_only=True)
        if (not isinstance(state,dict) or state.get('schema_version')!=1 or state.get('epoch')!=current['epoch']
                or state.get('identity')!=self.identity or len(state.get('history',[]))!=current['epoch']
                or state['stopping']['patience']!=stopping.patience or state['stopping']['min_delta']!=stopping.min_delta):
            raise ValueError('Invalid epoch state payload')
        model.load_state_dict(state['model']);optimizer.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        stopping.best=state['stopping']['best'];stopping.wait=state['stopping']['wait']
        random.setstate(state['rng']['python']);rng=state['rng']['numpy']
        np.random.set_state((rng[0],np.asarray(rng[1],dtype=np.uint32),rng[2],rng[3],rng[4]))
        torch.set_rng_state(state['rng']['torch'])
        if state['rng']['cuda'] is not None:
            if not torch.cuda.is_available() or len(state['rng']['cuda'])!=torch.cuda.device_count():raise ValueError('CUDA checkpoint device layout changed')
            torch.cuda.set_rng_state_all(state['rng']['cuda'])
        return state
