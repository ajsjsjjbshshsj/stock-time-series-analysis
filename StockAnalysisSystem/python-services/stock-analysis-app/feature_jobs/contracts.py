"""No caller paths, dates, SQL, model settings or publication switches."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from datetime import date
import re


class FeatureRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    kind:Literal['feature_build']
    preparation_id:str=Field(pattern=r'^[0-9a-f]{32}$')


def normalize(value):
    return FeatureRequest.model_validate(value).model_dump()


def validate_summary(value,task):
    fields={'task_id','purpose','verified','manifest_sha256','build_id','source_sha256',
        'output_sha256','row_count','stock_count','source_start','source_cutoff','activated','model_ready'}
    if (not isinstance(value,dict) or set(value)!=fields or value['task_id']!=task['id']
            or value['build_id']!=task['id'] or value['purpose']!='feature_build'
            or value['verified'] is not True or value['activated'] is not False or value['model_ready'] is not False):
        raise ValueError('Invalid feature candidate')
    for key in ('manifest_sha256','source_sha256','output_sha256'):
        if not isinstance(value[key],str) or re.fullmatch('[0-9a-f]{64}',value[key]) is None:
            raise ValueError('Invalid feature digest')
    if type(value['row_count']) is not int or value['row_count']<1 or type(value['stock_count']) is not int or not 1<=value['stock_count']<=10000:
        raise ValueError('Invalid feature counts')
    for key in ('source_start','source_cutoff'):
        if date.fromisoformat(value[key]).isoformat()!=value[key] or value[key]!=task['binding'][key]:
            raise ValueError('Feature range differs')
    return value
