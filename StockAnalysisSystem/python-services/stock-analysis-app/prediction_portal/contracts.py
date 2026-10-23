"""Client selects identities, never task kinds, paths, dates or training options."""
from pydantic import BaseModel,ConfigDict,Field

class PredictionRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    model_id:str=Field(pattern=r'^[0-9a-f]{32}$')
    pool_version:str=Field(pattern=r'^(model_[0-9a-f]{32}|pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$')

def normalize(value):return PredictionRequest.model_validate(value).model_dump()
