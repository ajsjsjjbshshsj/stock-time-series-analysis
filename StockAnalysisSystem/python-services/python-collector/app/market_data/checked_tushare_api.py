"""Checked Tushare wire protocol. No config imports, retries or global patches."""
from functools import partial
import math
import re

import pandas as pd
import requests

KINDS=frozenset({'NETWORK','MINUTE_QUOTA','HOURLY_QUOTA','PERMISSION','SCHEMA','PROVIDER'})
API_ROOT='http://api.waditu.com/dataapi'


class TushareRequestError(RuntimeError):
    """Only safe, whitelisted metadata crosses into shared pacing/diagnostics."""
    def __init__(self,kind: str,http_status: int | None=None):
        if kind not in KINDS or (http_status is not None and (type(http_status) is not int or not 100<=http_status<=599)):
            raise ValueError('Invalid safe Tushare error category/status')
        self.kind,self.http_status=kind,http_status
        super().__init__('TUSHARE_'+kind)


def _provider_kind(message):
    # Quota documentation often contains "权限详情"; it is not an auth failure.
    text=message.lower() if isinstance(message,str) else ''
    if any(s in text for s in ('每小时','小时最多','hourly','per hour','hour limit')): return 'HOURLY_QUOTA'
    if any(s in text for s in ('每分钟','分钟最多','频率','rate limit','too many requests','per minute')): return 'MINUTE_QUOTA'
    if any(s in text for s in ('token','权限','积分','unauthorized','forbidden','参数')): return 'PERMISSION'
    if any(s in text for s in ('timeout','timed out','connection','temporary','502','503','504')): return 'NETWORK'
    return 'PROVIDER'


class CheckedTushareApi:
    def __init__(self,token: str,timeout: float=20,session: object | None=None):
        if not isinstance(token,str) or not token.strip(): raise ValueError('Tushare credential unavailable')
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or timeout<=0:
            raise ValueError('Finite positive HTTP timeout required')
        self._token,self._timeout=token,float(timeout)
        self._session=session if session is not None else requests.Session()

    def __getattr__(self,name):
        if name.startswith('_'): raise AttributeError('Private API method unavailable')
        return partial(self.query,name)

    def query(self,api_name: str,fields: str='',**params) -> pd.DataFrame:
        if not isinstance(api_name,str) or not re.fullmatch(r'[a-z][a-z0-9_]*',api_name):
            raise ValueError('Invalid public Tushare API name')
        if not isinstance(fields,str): raise ValueError('Fields must be a string')
        payload=dict(api_name=api_name,token=self._token,params=params,fields=fields)
        try:
            response=self._session.post(API_ROOT+'/'+api_name,json=payload,timeout=self._timeout,allow_redirects=False)
        except (requests.exceptions.Timeout,requests.exceptions.ConnectionError):
            raise TushareRequestError('NETWORK') from None
        except requests.exceptions.RequestException:
            raise TushareRequestError('PROVIDER') from None
        status=response.status_code
        if type(status) is not int or not 100<=status<=599: raise TushareRequestError('SCHEMA')
        if status==408 or status>=500: raise TushareRequestError('NETWORK',status)
        if status in (400,401,403): raise TushareRequestError('PERMISSION',status)
        if status==429:
            kind='MINUTE_QUOTA'
            try:
                body=response.json()
                if isinstance(body,dict) and _provider_kind(body.get('msg'))=='HOURLY_QUOTA': kind='HOURLY_QUOTA'
            except ValueError: pass
            raise TushareRequestError(kind,status) from None
        if not 200<=status<300: raise TushareRequestError('PROVIDER',status)
        try: body=response.json()
        except ValueError: raise TushareRequestError('SCHEMA',status) from None
        if not isinstance(body,dict) or type(body.get('code')) is not int:
            raise TushareRequestError('SCHEMA',status)
        if body['code']!=0: raise TushareRequestError(_provider_kind(body.get('msg')),status)
        data=body.get('data')
        if not isinstance(data,dict): raise TushareRequestError('SCHEMA',status)
        columns,rows=data.get('fields'),data.get('items')
        if (not isinstance(columns,list) or any(not isinstance(c,str) or not c for c in columns)
            or len(columns)!=len(set(columns)) or not isinstance(rows,list)
            or any(not isinstance(row,list) or len(row)!=len(columns) for row in rows)
            or (not columns and rows)):
            raise TushareRequestError('SCHEMA',status)
        # Honest empty(0,0) remains undeclared; B1 still rejects that shape.
        return pd.DataFrame(rows,columns=columns)
