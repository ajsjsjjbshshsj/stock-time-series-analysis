"""Read-only artifact audit primitives; existing manifest bytes are never rewritten."""
import hashlib
import json
from pathlib import Path, PureWindowsPath
import re


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def digest_json(value, *, ensure_ascii=True):
    """Keep the caller's identity encoding; portfolio manifests historically escape Unicode."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        ensure_ascii=ensure_ascii, allow_nan=False).encode('utf-8')).hexdigest()


def model_output_paths(app, source, output, prefix, error):
    source, output = Path(source).resolve(), Path(output).resolve()
    parent = (Path(app) / 'models/transformer').resolve()
    if (output.parent != parent or not output.name.startswith(prefix)
            or source.is_relative_to(output) or output.is_relative_to(source)):
        raise ValueError(error)
    return source, output


def snapshot_hashes(root, *, excluded_root=None):
    root = Path(root).resolve()
    excluded = Path(excluded_root).resolve() if excluded_root is not None else None
    hashes = {}
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError('Artifact escaped its root')
        if excluded is None or not resolved.is_relative_to(excluded):
            hashes[path.relative_to(root).as_posix()] = sha256(path)
    return hashes


def check_hashes(root, hashes):
    """Accept legacy Windows keys safely without normalizing their stored identity hash."""
    if not isinstance(hashes, dict):
        raise ValueError('Invalid artifact hash manifest')
    root = Path(root).resolve()
    seen = set()
    for name, digest in hashes.items():
        if (not isinstance(name, str) or not name or PureWindowsPath(name).drive
                or name.startswith(('/', '\\')) or '\x00' in name
                or not isinstance(digest, str) or re.fullmatch('[0-9a-fA-F]{64}', digest) is None):
            raise ValueError('Invalid artifact hash entry')
        parts = name.replace('\\', '/').split('/')
        if any(part in ('', '.', '..') for part in parts):
            raise ValueError('Artifact path escaped its root')
        key = '/'.join(parts)
        if key in seen:
            raise ValueError('Duplicate artifact path alias')
        seen.add(key)
        path = root.joinpath(*parts).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256(path).lower() != digest.lower():
            raise ValueError(f'Old artifact changed or escaped its root: {name}')
    return len(hashes)
