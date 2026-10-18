"""Operator-reviewed dates backed by original issuer PDFs, not synthetic Tushare rows.

Registration is an explicit offline operation under the collector maintenance
lock. The caller reviews document semantics; hashes prove identity, not truth.
Training readers verify every document and completion seal without networking.
"""
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit

from app.market_data.forward_snapshot import read,write,digest
from app.nightly.contracts import is_a_share
from .contracts import identity,iso,timestamp

HOSTS={'static.cninfo.com.cn','disc.static.szse.cn','static.sse.com.cn',
       'www.sse.com.cn','star.sse.com.cn'}
MAX_PDF=16*1024*1024


def validate_review(value):
    if (not isinstance(value,dict) or set(value)!={'schema_version','kind','ts_code','full_days','documents','reviewed_at'}
            or type(value['schema_version']) is not int or value['schema_version']!=1
            or value['kind']!='reviewed_full_day_suspension' or not is_a_share(value['ts_code'])):
        raise ValueError('DISCLOSURE_REVIEW_SCHEMA')
    days=value['full_days'];docs=value['documents'];reviewed=timestamp(value['reviewed_at'])
    if (not isinstance(days,list) or not 1<=len(days)<=1000 or days!=sorted(set(days))
            or any(iso(d).isoformat()!=d or d<'2023-01-01' or iso(d)>reviewed.date() for d in days)
            or not isinstance(docs,list) or not 1<=len(docs)<=8):raise ValueError('DISCLOSURE_REVIEW_SCOPE')
    seen=set()
    for doc in docs:
        if not isinstance(doc,dict) or set(doc)!={'url','sha256'}:raise ValueError('DISCLOSURE_DOCUMENT_SCHEMA')
        url=urlsplit(doc['url'])
        if (url.scheme!='https' or url.hostname not in HOSTS or url.username or url.password or url.port not in (None,443)
                or url.query or url.fragment or not url.path.lower().endswith('.pdf')
                or not re.fullmatch('[0-9a-f]{64}',doc['sha256']) or doc['sha256'] in seen):
            raise ValueError('DISCLOSURE_DOCUMENT_IDENTITY')
        seen.add(doc['sha256'])
    return value


class DisclosureStore:
    def __init__(self,data_root):self.root=Path(data_root).resolve()/'suspension_reviews'

    def _folder(self,key):
        if not re.fullmatch('[0-9a-f]{64}',key):raise ValueError('DISCLOSURE_PATH')
        folder=self.root/key
        if self.root.is_symlink() or folder.is_symlink() or not folder.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('DISCLOSURE_PATH')
        return folder

    def _load(self,folder):
        try:
            folder=self._folder(folder.name)
            path=folder/'review.json';complete=folder/'complete.json'
            if path.is_symlink() or complete.is_symlink() or path.stat().st_size>256*1024:raise ValueError('DISCLOSURE_PATH')
            review=validate_review(read(path));key=identity(review)
            if folder.name!=key or read(complete)!=dict(review_sha256=digest(path)):raise ValueError('DISCLOSURE_SEAL')
            expected={'review.json','complete.json'}
            for doc in review['documents']:
                name=doc['sha256']+'.pdf';expected.add(name);pdf=folder/name
                if pdf.is_symlink() or not 0<pdf.stat().st_size<=MAX_PDF:raise ValueError('DISCLOSURE_DOCUMENT')
                data=pdf.read_bytes()
                if not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest()!=doc['sha256']:raise ValueError('DISCLOSURE_DOCUMENT_HASH')
            if {p.name for p in folder.iterdir()}!=expected:raise ValueError('DISCLOSURE_FILES')
            return dict(review_id=key,review=review)
        except (OSError,KeyError,TypeError) as error:
            raise ValueError('DISCLOSURE_UNVERIFIED') from None

    def load(self):
        if self.root.is_symlink():raise ValueError('DISCLOSURE_PATH')
        if not self.root.exists():return []
        return [self._load(path) for path in sorted(self.root.iterdir())]

    def register(self,review,documents):
        validate_review(review);key=identity(review);folder=self._folder(key)
        if folder.exists():return self._load(folder)
        if set(documents)!={doc['sha256'] for doc in review['documents']}:raise ValueError('DISCLOSURE_DOCUMENT_SET')
        for sha,data in documents.items():
            if not isinstance(data,bytes) or not 0<len(data)<=MAX_PDF or not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest()!=sha:
                raise ValueError('DISCLOSURE_DOCUMENT_HASH')
        folder.mkdir(parents=True)
        for sha,data in documents.items():
            with (folder/(sha+'.pdf')).open('xb') as stream:stream.write(data)
        write(folder/'review.json',review,exclusive=True)
        write(folder/'complete.json',dict(review_sha256=digest(folder/'review.json')),exclusive=True)
        return self._load(folder)
