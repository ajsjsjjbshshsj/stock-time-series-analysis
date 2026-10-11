"""Lazy checked source acquisitions reusing the original bounded request engine."""
from pathlib import Path
import time

from app.market_data.repair_acquisition import collect_requests
from app.market_data.forward_snapshot import Requests,read
from app.market_data.csi300_constituent_snapshot import migrate_request_state
from .service import PreparationFailure


class LazyClient:
    def __init__(self,factory): self.factory=factory; self.client=None
    def __getattr__(self,name):
        def call(**params):
            if self.client is None: self.client=self.factory()
            return getattr(self.client,name)(**params)
        return call


def source_failure(state):
    kind=state.get('failure','PROVIDER')
    return PreparationFailure(kind,retry_not_before=state.get('retry_not_before'),http_status=state.get('http_status'))


class MarketAcquirer:
    def __init__(self,client_factory,request_state,*,interval=.5,clock=time.time,sleep=time.sleep):
        self.client_factory=client_factory; self.request_state=Path(request_state)
        self.interval=interval; self.clock=clock; self.sleep=sleep
        self.on_progress=None

    def requests(self,directory):
        directory=Path(directory); directory.mkdir(parents=True,exist_ok=True)
        try:
            migrate_request_state(self.request_state)
            return Requests(directory,self.interval,self.clock,self.sleep,self.request_state)
        except RuntimeError:
            if self.request_state.with_suffix('.lock').exists():
                raise PreparationFailure('REQUEST_STATE_BUSY',retry_not_before=self.clock()+60) from None
            state=read(self.request_state) if self.request_state.exists() else {}
            raise source_failure(state) from None

    def collect(self,requests,directory):
        if not requests: return dict(complete=True,shards=[])
        result=collect_requests(LazyClient(self.client_factory),requests,Path(directory),self.request_state,
            self.interval,self.clock,self.sleep,allowed_markets=('SH','SZ','BJ'),on_progress=self.on_progress)
        if not result['complete']:
            local=Path(directory)/'acquisition.json'
            state=read(local) if local.exists() else read(self.request_state) if self.request_state.exists() else {}
            kind=result['status']
            if kind=='BLOCKED': kind='REQUEST_STATE_BUSY' if self.request_state.with_suffix('.lock').exists() else state.get('failure','PROVIDER')
            state=dict(state,failure=kind)
            raise source_failure(state)
        return result
