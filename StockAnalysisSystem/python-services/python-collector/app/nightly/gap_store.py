"""Immutable batch identities and atomic, root-locked progress checkpoints."""
from pathlib import Path
import json, os, re, uuid
from app.preparation.contracts import identity
from app.preparation.worker import require_root_lock
from app.preparation.gap_plan import validate_coverage, plan_basic
from app.market_data.forward_snapshot import read, write


class GapBatchStore:
    def __init__(self, data_root):
        self.root = Path(data_root).resolve()

    def _path(self, prefix):
        if type(prefix) is not str or re.fullmatch('[0-9a-f]{16}', prefix) is None:
            raise ValueError('GAP_BATCH_ID_INVALID')
        path = (self.root/'gap_batches'/prefix).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('GAP_BATCH_OUTSIDE_ROOT')
        return path

    def save(self, path, body, *, immutable=False):
        require_root_lock(self.root)
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('GAP_FILE_OUTSIDE_ROOT')
        if immutable and path.exists():
            if self.read(path) != body:
                raise ValueError('GAP_IMMUTABLE_BINDING_CHANGED')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('x', encoding='utf-8') as stream:
                json.dump(dict(body=body, sha256=identity(body)), stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():temp.unlink()

    def read(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('GAP_FILE_OUTSIDE_ROOT')
        value = read(path)
        if set(value) != {'body', 'sha256'} or value['sha256'] != identity(value['body']):
            raise ValueError('GAP_FILE_HASH_INVALID')
        return value['body']

    def create_or_load(self, snapshot, daily_plan):
        require_root_lock(self.root)
        validate_coverage(snapshot)
        body = dict(strategy='market-gap-v1', snapshot=snapshot, daily_plan=daily_plan,
                    basic_plan=plan_basic(snapshot, []))
        full = identity(body)
        root = self._path(full[:16])
        path = root/'binding.json'
        if path.exists():
            old = self.read(path)
            if old['full_sha256'] != full:
                raise ValueError('GAP_BATCH_HASH_PREFIX_COLLISION')
            if old['binding'] != body:
                raise ValueError('GAP_BATCH_BINDING_CHANGED')
        else:
            self.save(path, dict(full_sha256=full, binding=body), immutable=True)
        self.save(root/'identity.json', dict(version='market-gap-v1:'+full[:16], full_sha256=full), immutable=True)
        return dict(batch_id='market-gap-v1:'+full[:16], full_sha256=full, path=str(root), **body)

    def load(self, version):
        from app.nightly.contracts import schema_for_version
        schema_for_version(version)
        if not version.startswith('market-gap-v1:'):
            raise ValueError('GAP_BATCH_VERSION_INVALID')
        root = self._path(version.split(':')[1])
        body = self.read(root/'binding.json')
        full, binding = body['full_sha256'], body['binding']
        if identity(binding) != full or version != 'market-gap-v1:'+full[:16]:
            raise ValueError('GAP_BATCH_HASH_INVALID')
        validate_coverage(binding['snapshot'])
        return dict(batch_id=version, full_sha256=full, path=str(root), **binding)

    def check_version(self, version):
        from app.nightly.contracts import schema_for_version
        schema_for_version(version)
        if not version.startswith('market-gap-v1:'):raise ValueError('GAP_BATCH_VERSION_INVALID')
        body = self.read(self._path(version.split(':')[1])/'identity.json')
        if (set(body) != {'version', 'full_sha256'} or body['version'] != version
                or re.fullmatch('[0-9a-f]{64}', body['full_sha256']) is None
                or version != 'market-gap-v1:'+body['full_sha256'][:16]):
            raise ValueError('GAP_BATCH_HASH_INVALID')
        return body

    def store_followup_plan(self, batch, source_request_id, basic_plan):
        if type(source_request_id) is not str or re.fullmatch('[0-9a-f]{64}', source_request_id) is None:
            raise ValueError('GAP_FOLLOWUP_PARENT_INVALID')
        current = self.load(batch['batch_id'])
        if current['full_sha256'] != batch['full_sha256']:
            raise ValueError('GAP_BATCH_BINDING_CHANGED')
        body = dict(batch_sha256=batch['full_sha256'], source_request_id=source_request_id, requests=basic_plan)
        self.save(Path(batch['path'])/'followups'/(source_request_id+'.json'), body, immutable=True)
        return body
