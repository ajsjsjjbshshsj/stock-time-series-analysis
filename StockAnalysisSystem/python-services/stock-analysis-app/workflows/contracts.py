"""Bounded, versioned model operations, independent of engineering contracts."""
from datetime import date
import re
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from tasks.contracts import identity,encoded

ALGORITHMS=('xgboost','lightgbm','transformer')
TRIALS=('raw_rmse20','raw_rmse300','raw_rankic300','csrank_rankic300')


class Operation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    kind:Literal['model_workflow']='model_workflow'
    operation:Literal['train','predict','backtest','import']
    algorithm:Literal['xgboost','lightgbm','transformer']
    start_date:str|None=None
    end_date:str|None=None
    ts_code:str|None=Field(default=None,pattern=r'^[0-9]{6}\.(SH|SZ)$')
    stock_codes:list[str]|None=None
    model_id:str|None=None
    pool_version:str|None=Field(default=None,pattern=r'^(pool_[0-9a-f]{32}|csi300_[0-9a-f]{16})$')
    import_task_id:str|None=None
    experiment_id:str|None=None
    trial:Literal['raw_rmse20','raw_rmse300','raw_rankic300','csrank_rankic300']|None=None
    n_estimators:int=Field(default=20,ge=1,le=300)
    max_depth:int=Field(default=3,ge=1,le=6)
    num_epochs:int=Field(default=1,ge=1,le=5)
    batch_size:int=Field(default=1,ge=1,le=2)
    use_probe_selection:Literal[True]=True
    label:Literal['raw','daily_rank']='raw'
    early_metric:Literal['rmse','rank_ic']='rmse'
    patience:int=Field(default=30,ge=1,le=50)
    top_n:int=Field(default=10,ge=1,le=100)
    initial_capital:float=Field(default=1000000.,gt=0,le=1e10)
    fee_bps:float=Field(default=3.,ge=0,le=100)
    slippage_bps:float=Field(default=10.,ge=0,le=100)

    @model_validator(mode='after')
    def boundaries(self):
        for value in (self.model_id,self.import_task_id,self.experiment_id):
            if value is not None:identity(value)
        if self.operation=='import':
            if self.pool_version is not None:raise ValueError('Imported model dictates its own pool')
            if self.model_id or bool(self.import_task_id)==bool(self.experiment_id):raise ValueError('One import origin required')
            if self.experiment_id and (self.algorithm!='lightgbm' or self.trial is None):raise ValueError('Validation trial required')
        else:
            if self.import_task_id or self.experiment_id or self.trial:raise ValueError('Unexpected import origin')
            if self.start_date is None or self.end_date is None:raise ValueError('Explicit date range required')
            first,last=date.fromisoformat(self.start_date),date.fromisoformat(self.end_date)
            if first.isoformat()!=self.start_date or last.isoformat()!=self.end_date or not 0<=(last-first).days<=1826:
                raise ValueError('Invalid workflow dates')
            if (self.operation=='train')==(self.model_id is not None):raise ValueError('Model selection does not match operation')
            if self.operation=='train':
                if self.algorithm=='xgboost' and (self.ts_code is None or self.n_estimators>100):raise ValueError('Single-stock XGBoost required')
                if self.algorithm=='lightgbm' and (self.ts_code or self.stock_codes):raise ValueError('LightGBM uses fixed full market')
                if self.algorithm=='transformer':
                    codes=self.stock_codes
                    if (self.ts_code or not codes or not 10<=len(codes)<=20 or codes!=sorted(set(codes))
                            or any(re.fullmatch(r'[0-9]{6}\.(SH|SZ)',c) is None for c in codes)):
                        raise ValueError('Fixed small Transformer pool required')
        return self


def normalize_operation(value):
    result=Operation.model_validate(value).model_dump()
    # Absent selections retain the legacy request/digest byte shape.
    if result['pool_version'] is None:result.pop('pool_version')
    return result


def validate_summary(value):
    if not isinstance(value,dict) or set(value)!={'task_id','purpose','verified','manifest_sha256','index'}:
        raise ValueError('Invalid workflow summary')
    identity(value['task_id'])
    if (value['purpose']!='model_workflow' or value['verified'] is not True
            or not isinstance(value['manifest_sha256'],str) or re.fullmatch('[0-9a-f]{64}',value['manifest_sha256']) is None):
        raise ValueError('Unverified workflow')
    index=value['index']
    if not isinstance(index,dict) or set(index)!={'artifact_kind','algorithm','model_id','created_at','title','metadata'}:
        raise ValueError('Invalid workflow index')
    identity(index['model_id'])
    if (index['artifact_kind'] not in ('model','result') or index['algorithm'] not in ALGORITHMS
            or type(index['created_at']) is not int or index['created_at']<0
            or not isinstance(index['title'],str) or not 1<=len(index['title'])<=120
            or not isinstance(index['metadata'],dict) or len(encoded(index).encode())>48000
            or index['metadata'].get('status') not in ('COMPLETE','NOT_READY')):
        raise ValueError('Invalid workflow index metadata')
    if index['artifact_kind']=='model' and index['model_id']!=value['task_id']:raise ValueError('Model identity mismatch')
    encoded(value)  # Recursively disallow nonfinite JSON values.
    return value
