"""Read-only adapter to the collector's existing sealed evidence contracts."""
import hashlib
import os
from pathlib import Path
import sys
from sqlalchemy import text,bindparam
from sqlalchemy.orm import sessionmaker
from .contracts import encoded


def collector_root():
    return Path(__file__).resolve().parents[2]/'python-collector'


class SuspensionEvidence:
    def __init__(self,engine,root=None):
        # The sibling service is source-distributed, not an installed wheel.
        # Resolve it from the project, never a developer-specific disk path.
        service=collector_root()
        if str(service) not in sys.path:sys.path.append(str(service))
        from app.preparation.evidence import EvidenceStore
        from app.repositories.source_evidence_repository import SourceEvidenceRepository
        from app.runtime_paths import RuntimePaths
        project=Path(__file__).resolve().parents[3]
        if root is None:root=RuntimePaths.from_environment(project,os.environ).data_root
        self.engine=engine;self.repository=SourceEvidenceRepository(sessionmaker(bind=engine))
        self.store=EvidenceStore(root,self.repository)

    def identity_for_pool(self,codes,start,end):
        digest=hashlib.sha256()
        statement=text("SELECT * FROM market_source_evidence WHERE kind='suspend_d' AND ts_code IN :codes "
                       'AND start_date<=:end AND end_date>=:start ORDER BY ts_code,start_date,evidence_id').bindparams(bindparam('codes',expanding=True))
        with self.engine.connect() as c:
            for offset in range(0,len(codes),500):
                rows=c.execute(statement,dict(codes=codes[offset:offset+500],start=start,end=end)).mappings()
                for row in rows:
                    value=dict(row)
                    for key in ('start_date','end_date'):value[key]=str(value[key])
                    digest.update(encoded(value).encode()+b'\n')
        return digest.hexdigest()

    def for_stock(self,code,start,end):
        from data_processor.feature_store_contract import validate_stock_code,date_only
        validate_stock_code(code);date_only(start);date_only(end)
        refs=self.repository.list('suspend_d',[code],start,end)
        body=self.store.load(refs,codes=[code],start=start,end=end)
        queried=set(body['queried_suspensions']);by_day={}
        for row in body['suspend_d']:by_day.setdefault(row['trade_date'],[]).append(row)
        full_days=sorted(day for day,rows in by_day.items() if (code,day) in queried
            and rows and all(r['suspend_type']=='S' and r['suspend_timing'] is None for r in rows))
        return dict(full_days=full_days,evidence_ids=sorted(ref['evidence_id'] for ref in refs))
