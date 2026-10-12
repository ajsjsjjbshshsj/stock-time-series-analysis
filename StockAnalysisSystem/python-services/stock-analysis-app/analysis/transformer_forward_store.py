"""Exclusive immutable signal packets; a receipt attests completed payload visibility."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import uuid

from analysis.transformer_forward_contracts import iso_day,classify_publication,aware
from analysis.transformer_forward_inference import validate_scores


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'),
                      parse_constant=lambda _:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def identity(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def write_exclusive(path,value):
    encoded=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
    with Path(path).open('xb') as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


class ForwardSignalStore:
    def __init__(self,root,binding):
        self.root=Path(root).resolve()
        if not self.root.name.startswith('forward_signals_'):
            raise ValueError('Separate forward_signals_* output required')
        self.binding=deepcopy(binding)
        identity(self.binding)
        models=binding.get('models',[])
        if (len(models)!=6 or {(m.get('seed'),m.get('mode')) for m in models}
                != {(s,m) for s in (42,123,2026) for m in ('adjusted','unadjusted_control')}
                or any(type(m.get('seed')) is not int for m in models)
                or len(binding.get('codes',[]))!=20 or binding['codes']!=sorted(set(binding['codes']))):
            raise ValueError('Store requires frozen six-model identity and twenty-stock universe')
        iso_day(binding['cutoff'])
        aware(datetime.fromisoformat(binding['frozen_at']))
        self._active=set()
        self.root.mkdir(parents=True,exist_ok=True)
        path=self.root/'binding.json'
        if path.exists():
            if read(path)!=self.binding:
                raise ValueError('Store source binding mismatch')
        else:
            if any(self.root.iterdir()):
                raise ValueError('Unidentified partial signal store preserved')
            write_exclusive(path,self.binding)

    def _path(self,*parts):
        result=self.root.joinpath(*parts)
        if not result.resolve().is_relative_to(self.root):
            raise ValueError('Signal path escapes output root')
        return result

    @contextmanager
    def lock(self,signal_date):
        iso_day(signal_date)
        path=self._path('locks',signal_date+'.lock')
        path.parent.mkdir(parents=True,exist_ok=True)
        try:
            fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError:
            raise RuntimeError('Signal date busy or residual lock; preserve evidence') from None
        try:
            os.write(fd,str(os.getpid()).encode('ascii'))
            os.fsync(fd)
            self._active.add(signal_date)
            yield
        finally:
            self._active.discard(signal_date)
            os.close(fd)
            path.unlink()

    def new_attempt(self,signal_date):
        iso_day(signal_date)
        if signal_date not in self._active:
            raise RuntimeError('Acquire the signal-date lock first')
        if self._path('signals',signal_date).exists():
            raise ValueError('Existing publication preserved; verify only, never repredict')
        path=self._path('attempts',signal_date,uuid.uuid4().hex)
        path.mkdir(parents=True,exist_ok=False)
        return path

    def _validate(self,signal_date,payload):
        if payload.get('signal_date')!=signal_date or not isinstance(payload.get('models'),list):
            raise ValueError('Signal payload identity missing')
        expected={(m['seed'],m['mode']):m for m in self.binding['models']}
        models=payload['models']
        keys=[(m.get('seed'),m.get('mode')) for m in models]
        if len(keys)!=6 or set(keys)!=set(expected) or any(type(m.get('seed')) is not int for m in models):
            raise ValueError('Signal payload needs exactly six frozen model identities')
        result=[]
        for model in sorted(models,key=lambda m:(m['seed'],m['mode'])):
            key=(model['seed'],model['mode'])
            summary=validate_scores(model['scores'],self.binding['codes'],signal_date,expected[key]['model_sha256'])
            result.append(dict(seed=key[0],mode=key[1],**summary))
        return result

    def _files(self,folder):
        result={}
        for path in sorted(folder.rglob('*')):
            if not path.resolve().is_relative_to(folder.resolve()):
                raise ValueError('Packet evidence escapes its directory')
            if path.is_file() and path.name not in ('marker.json','receipt.json','seal.json'):
                result[path.relative_to(folder).as_posix()]=digest(path)
        return result

    def publish(self,signal_date,attempt,payload,*,calendar,frozen_at,clock):
        iso_day(signal_date)
        if signal_date not in self._active:
            raise RuntimeError('Publication requires the signal-date lock')
        attempt=Path(attempt).resolve()
        base=self._path('attempts',signal_date).resolve()
        if attempt.parent!=base or not attempt.is_dir():
            raise ValueError('Publication attempt is not owned by this date/store')
        if aware(frozen_at)!=aware(datetime.fromisoformat(self.binding['frozen_at'])):
            raise ValueError('Frozen timestamp differs from bound model group')
        summaries=self._validate(signal_date,payload)
        destination=self._path('signals',signal_date)
        if destination.exists():
            raise ValueError('Existing signal publication cannot be overwritten')
        write_exclusive(attempt/'payload.json',payload)
        marker=dict(signal_date=signal_date,binding_sha256=identity(self.binding),files=self._files(attempt))
        write_exclusive(attempt/'marker.json',marker)
        destination.parent.mkdir(parents=True,exist_ok=True)
        # The owning date lock prevents another cooperating publisher creating
        # this destination. Never replace an existing destination directory.
        os.rename(attempt,destination)
        published_at=aware(clock())
        classification=classify_publication(signal_date,calendar,published_at,frozen_at,self.binding['cutoff'])
        receipt=dict(classification,calendar=calendar,frozen_at=aware(frozen_at).isoformat(),
                     marker_sha256=digest(destination/'marker.json'),binding_sha256=identity(self.binding),
                     model_summaries=summaries)
        write_exclusive(destination/'receipt.json',receipt)
        seal=dict(marker_sha256=digest(destination/'marker.json'),receipt_sha256=digest(destination/'receipt.json'))
        write_exclusive(destination/'seal.json',seal)
        return self.verify(signal_date)

    def verify(self,signal_date):
        iso_day(signal_date)
        folder=self._path('signals',signal_date)
        try:
            if read(self.root/'binding.json')!=self.binding:
                raise ValueError('Signal store binding changed')
            marker,receipt,seal=(read(folder/name) for name in ('marker.json','receipt.json','seal.json'))
            expected_seal=dict(marker_sha256=digest(folder/'marker.json'),receipt_sha256=digest(folder/'receipt.json'))
            if seal!=expected_seal:
                raise ValueError('Signal receipt/marker seal mismatch')
            expected_marker=dict(signal_date=signal_date,binding_sha256=identity(self.binding),files=self._files(folder))
            if marker!=expected_marker or 'payload.json' not in marker['files']:
                raise ValueError('Signal payload/evidence hashes changed')
            summaries=self._validate(signal_date,read(folder/'payload.json'))
            frozen_at=datetime.fromisoformat(self.binding['frozen_at'])
            classification=classify_publication(signal_date,receipt['calendar'],datetime.fromisoformat(receipt['published_at']),
                                               frozen_at,self.binding['cutoff'])
            expected_receipt=dict(classification,calendar=receipt['calendar'],frozen_at=aware(frozen_at).isoformat(),
                                 marker_sha256=expected_seal['marker_sha256'],binding_sha256=identity(self.binding),
                                 model_summaries=summaries)
            if receipt!=expected_receipt:
                raise ValueError('Signal receipt classification/model identities changed')
            return dict(classification,models=6,model_summaries=summaries,
                        receipt_sha256=expected_seal['receipt_sha256'])
        except (OSError,KeyError,TypeError,json.JSONDecodeError) as exc:
            raise ValueError('Incomplete or malformed signal packet; preserve all evidence') from exc
