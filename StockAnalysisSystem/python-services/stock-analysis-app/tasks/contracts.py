"""Small strict task contract; no caller commands, paths or arbitrary model kwargs."""
from datetime import date
import hashlib
import json
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

TERMINAL=frozenset(('SUCCEEDED','FAILED','CANCELLED','INTERRUPTED'))
STAGES=frozenset(('QUEUED','PREFLIGHT','INPUT','PROBE','TRAIN','VERIFY','SAVE','DONE'))
EVENTS=STAGES | TERMINAL | {'CREATED','CANCEL_REQUESTED','RECOVERED','PROGRESS'}


class Conflict(ValueError): pass
class NotFound(LookupError): pass
class LeaseLost(RuntimeError): pass
class TaskFailure(RuntimeError):
    def __init__(self,code):
        self.code=code if code in {'SOURCE_CHANGED','INPUT_INVALID','TRAINING_FAILED','CANDIDATE_INVALID',
            'PROCESS_FAILED','PROTOCOL_INVALID','INTERRUPTED','DEPENDENCY_UNAVAILABLE',
            'RESOURCE_UNAVAILABLE','RESOURCE_LIMIT','TIME_LIMIT'} else 'TRAINING_FAILED'
        super().__init__(self.code)


def encoded(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def digest(value): return hashlib.sha256(encoded(value).encode('utf-8')).hexdigest()


def identity(value):
    if not isinstance(value,str) or re.fullmatch('[0-9a-f]{32}',value) is None:
        raise ValueError('Invalid task identity')
    return value


def bound_integer(value,minimum=1,maximum=100):
    if type(value) is not int or not minimum<=value<=maximum: raise ValueError('Invalid bound')
    return value


class JobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind: Literal['xgboost_single']='xgboost_single'
    purpose: Literal['engineering_smoke']='engineering_smoke'
    ts_code: str=Field(pattern=r'^[0-9]{6}\.(SH|SZ)$')
    start_date: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    n_estimators: int=Field(default=20,ge=1,le=100)
    max_depth: int=Field(default=3,ge=1,le=6)

    @model_validator(mode='after')
    def dates(self):
        if date.fromisoformat(self.start_date)>date.fromisoformat(self.end_date):
            raise ValueError('Invalid task range')
        return self


class MarketJobRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind: Literal['lightgbm_all_market']
    purpose: Literal['engineering_smoke']='engineering_smoke'
    scope: Literal['all_shsz_a']='all_shsz_a'
    profile: Literal['bounded_v1']='bounded_v1'
    start_date: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    end_date: str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')

    @model_validator(mode='after')
    def dates(self):
        if date.fromisoformat(self.start_date)>date.fromisoformat(self.end_date):
            raise ValueError('Invalid task range')
        return self


def normalize_request(value):
    if isinstance(value,dict) and value.get('kind')=='model_workflow':
        from workflows.contracts import normalize_operation
        return normalize_operation(value)
    model=MarketJobRequest if isinstance(value,dict) and value.get('kind')=='lightgbm_all_market' else JobRequest
    return model.model_validate(value).model_dump()


def idempotency(value):
    if not isinstance(value,str) or re.fullmatch('[A-Za-z0-9_-]{16,64}',value) is None:
        raise ValueError('Invalid idempotency key')
    return value
