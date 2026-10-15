"""Validated deployment settings; protected mode never invents credentials."""
from dataclasses import dataclass,field
import ipaddress
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AuthSettings:
    mode: str='local'
    origin: str=''
    secret: str=field(default='',repr=False)
    allow_local_http: bool=False
    trusted_proxies: tuple=()

    def __post_init__(self):
        if self.mode not in ('local','protected'): raise ValueError('Invalid AUTH_MODE')
        for network in self.trusted_proxies: ipaddress.ip_network(network)
        if self.mode=='local': return
        parsed=urlsplit(self.origin)
        if (not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment
                or self.origin!=f'{parsed.scheme}://{parsed.netloc}' or len(self.secret.encode())<32):
            raise ValueError('Protected auth configuration incomplete')
        if parsed.scheme!='https' and not (self.allow_local_http and parsed.scheme=='http'
                and parsed.hostname in ('localhost','127.0.0.1','::1')):
            raise ValueError('Protected remote origin requires HTTPS')

    @property
    def secure(self): return self.origin.startswith('https://')

    @property
    def cookie(self): return '__Host-sas_session' if self.secure else 'sas_local_session'

    @classmethod
    def from_env(cls):
        from pathlib import Path
        from dotenv import load_dotenv
        app=Path(__file__).resolve().parents[2]
        for path in (app/'.env',app.parents[1]/'.env'):
            if path.is_file():
                load_dotenv(path,override=False)
                break
        return cls(mode=os.getenv('AUTH_MODE','local'),origin=os.getenv('AUTH_ORIGIN',''),
            secret=os.getenv('AUTH_SECRET',''),allow_local_http=os.getenv('AUTH_ALLOW_LOCAL_HTTP')=='true',
            trusted_proxies=tuple(v.strip() for v in os.getenv('AUTH_TRUSTED_PROXIES','').split(',') if v.strip()))
