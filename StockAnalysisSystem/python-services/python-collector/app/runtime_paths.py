"""Portable runtime references; legacy roots are explicit, never guessed/resealed.

Only configuration may contain machine absolute roots. Persisted new references
use runtime:// or project:// and every resolved suffix stays inside its root.
This module has no config/SDK/SQL import and performs no writes.
"""
from pathlib import Path, PurePosixPath, PureWindowsPath
import json
import re


def _absolute_parts(value):
    if type(value) is not str or not value or '\0' in value:
        raise ValueError('RUNTIME_PATH_INVALID')
    windows=bool(re.match(r'^[A-Za-z]:',value) or value.startswith('\\\\'))
    flavor=PureWindowsPath if windows else PurePosixPath
    path=flavor(value)
    if not path.is_absolute():raise ValueError('RUNTIME_ABSOLUTE_ROOT_REQUIRED')
    # Inspect before pathlib can normalize away a dot/traversal component.
    if any(part in ('.','..') for part in re.split(r'[\\/]',value)):
        raise ValueError('RUNTIME_PATH_TRAVERSAL')
    parts=tuple(part.casefold() for part in path.parts) if windows else path.parts
    return windows,parts,path.parts


def _suffix(value):
    if (type(value) is not str or not value or any(char in value for char in '\\:\0?#')
            or value.startswith('/') or any(part in ('','.','..') or part.endswith((' ','.')) for part in value.split('/'))):
        raise ValueError('RUNTIME_REFERENCE_INVALID')
    return value


class RuntimePaths:
    def __init__(self,project_root,*,data_root=None,legacy_runtime_roots=(),legacy_project_roots=()):
        self.project_root=Path(project_root).resolve()
        configured=Path(data_root) if data_root is not None else Path('.runtime/stock_pool_preparation')
        self.data_root=(configured if configured.is_absolute() else self.project_root/configured).resolve()
        self._roots={'runtime':self.data_root,'project':self.project_root}
        self._legacy=[]
        for kind,values in (('runtime',legacy_runtime_roots),('project',legacy_project_roots)):
            if not isinstance(values,(list,tuple)):
                raise ValueError('RUNTIME_MAPPING_SCHEMA_INVALID')
            for value in values:
                windows,parts,_=_absolute_parts(value)
                for prior_kind,prior_windows,prior_parts in self._legacy:
                    if windows==prior_windows and (parts==prior_parts or prior_kind==kind and (
                            parts[:len(prior_parts)]==prior_parts or prior_parts[:len(parts)]==parts)):
                        raise ValueError('RUNTIME_MAPPING_AMBIGUOUS')
                self._legacy.append((kind,windows,parts))

    @classmethod
    def from_environment(cls,project_root,env):
        project=Path(project_root).resolve()
        values={}
        mapping=env.get('RUNTIME_PATH_MAP_FILE')
        if mapping:
            path=Path(mapping)
            path=(path if path.is_absolute() else project/path).resolve()
            try:body=json.loads(path.read_text(encoding='utf-8'))
            except (OSError,ValueError):raise ValueError('RUNTIME_MAPPING_FILE_INVALID') from None
            if (not isinstance(body,dict) or set(body)!={'schema_version','legacy_runtime_roots','legacy_project_roots'}
                    or type(body['schema_version']) is not int or body['schema_version']!=1):
                raise ValueError('RUNTIME_MAPPING_SCHEMA_INVALID')
            values={key:body[key] for key in ('legacy_runtime_roots','legacy_project_roots')}
        return cls(project,data_root=env.get('PREPARATION_DATA_ROOT') or None,**values)

    def _inside(self,kind,suffix,*,must_exist=False):
        root=self._roots[kind]
        path=(root/_suffix(suffix)).resolve()
        if not path.is_relative_to(root):raise ValueError('RUNTIME_PATH_OUTSIDE_ROOT')
        if must_exist and not path.exists():raise ValueError('RUNTIME_REFERENCE_MISSING')
        return path

    def encode(self,path,*,root='runtime'):
        if root not in self._roots:raise ValueError('RUNTIME_ROOT_UNKNOWN')
        actual=Path(path).resolve()
        try:relative=actual.relative_to(self._roots[root]).as_posix()
        except ValueError:raise ValueError('RUNTIME_PATH_OUTSIDE_ROOT') from None
        return root+'://'+_suffix(relative)

    def resolve(self,reference,*,must_exist=False):
        if type(reference) is not str or not reference:
            raise ValueError('RUNTIME_REFERENCE_INVALID')
        if '://' in reference:
            kind,suffix=reference.split('://',1)
            if kind not in self._roots:raise ValueError('RUNTIME_ROOT_UNKNOWN')
            return self._inside(kind,suffix,must_exist=must_exist)
        windows,parts,original=_absolute_parts(reference)
        candidates=[]
        for kind,root in self._roots.items():
            root_windows,root_parts,_=_absolute_parts(str(root))
            if windows==root_windows and parts[:len(root_parts)]==root_parts:
                candidates.append((len(root_parts),kind))
        for kind,root_windows,root_parts in self._legacy:
            if windows==root_windows and parts[:len(root_parts)]==root_parts:
                candidates.append((len(root_parts),kind))
        if not candidates:raise ValueError('RUNTIME_LEGACY_MAPPING_REQUIRED')
        length,kind=max(candidates)
        suffix='/'.join(original[length:])
        return self._inside(kind,suffix,must_exist=must_exist)
