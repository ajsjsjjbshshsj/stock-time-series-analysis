"""Dedicated repair repository: separate table inventory, no legacy upserts."""
from sqlalchemy import bindparam, text
from sqlalchemy.exc import IntegrityError
from pathlib import Path
import os
import uuid

from app.market_data.repair_contract import canonical, identity, load_baseline, DAILY, BASIC
from app.market_data.forward_snapshot import read, write, digest
from app.market_data.repair_snapshot import import_candidates, equal_business


class MarketRepairRepository:
    def __init__(self,session_factory):
        self.session_factory=session_factory

    def capture_baseline(self,codes: list[str],start: str,end: str) -> dict:
        if not codes or len(codes)!=len(set(codes)): raise ValueError('Distinct stock identities required')
        result=dict(start=start,end=end,schema={})
        with self.session_factory() as session:
            for table in ('stock_daily','stock_daily_basic'):
                # Identifiers are fixed literals; all external values are bound.
                columns=session.execute(text('SELECT COLUMN_NAME,COLUMN_TYPE FROM information_schema.COLUMNS '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY ORDINAL_POSITION'),dict(table=table)).mappings().all()
                indexes=session.execute(text('SELECT INDEX_NAME,COLUMN_NAME,SEQ_IN_INDEX,NON_UNIQUE FROM information_schema.STATISTICS '
                    'WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:table ORDER BY INDEX_NAME,SEQ_IN_INDEX'),dict(table=table)).mappings().all()
                unique={}
                for r in indexes:
                    if not r['NON_UNIQUE']: unique.setdefault(r['INDEX_NAME'],[]).append(r['COLUMN_NAME'])
                result['schema'][table]=dict(columns=[r['COLUMN_NAME'] for r in columns],
                    types={r['COLUMN_NAME']:r['COLUMN_TYPE'] for r in columns},unique_keys=list(unique.values()))
                stmt=text(f'SELECT * FROM `{table}` WHERE ts_code IN :codes AND trade_date BETWEEN :start AND :end '
                    'ORDER BY ts_code,trade_date').bindparams(bindparam('codes',expanding=True))
                result[table]=[dict(r) for r in session.execute(stmt,dict(codes=codes,start=start,end=end)).mappings().all()]
            stmt=text('SELECT * FROM stock_basic WHERE ts_code IN :codes ORDER BY ts_code').bindparams(bindparam('codes',expanding=True))
            result['stock_basic']=[dict(r) for r in session.execute(stmt,dict(codes=codes)).mappings().all()]
        return result

    @staticmethod
    def _assert_original(original,current):
        if original['schema']!=current['schema']: raise ValueError('Database schema changed')
        for table in ('stock_daily','stock_daily_basic'):
            actual={(r['ts_code'],str(r['trade_date'])):canonical(r) for r in current[table]}
            for row in original[table]:
                k=row['ts_code'],str(row['trade_date'])
                if actual.get(k)!=canonical(row): raise ValueError('Original database row changed or missing')

    @staticmethod
    def _get_row(session,table,candidate):
        if table not in ('stock_daily','stock_daily_basic'): raise ValueError('Repair table not allowed')
        # MySQL current read, not a stale repeatable-read snapshot after a race.
        suffix=' FOR UPDATE' if session.get_bind().dialect.name=='mysql' else ''
        rows=session.execute(text(f'SELECT * FROM `{table}` WHERE ts_code=:ts_code AND trade_date=:trade_date'+suffix),
            {k:candidate[k] for k in ('ts_code','trade_date')}).mappings().all()
        if len(rows)>1: raise ValueError('Duplicate database key')
        return dict(rows[0]) if rows else None

    def _insert_batch(self,table,rows,types):
        allowed=set(DAILY if table=='stock_daily' else BASIC+['source'] if table=='stock_daily_basic' else [])
        if not allowed or any(not {'ts_code','trade_date'}<=set(r)<=allowed for r in rows):
            raise ValueError('Invalid insert columns/table')
        result=dict(inserted=0,already_equal=0,conflicts=[])
        with self.session_factory() as session:
            try:
                # sqlite legacy driver defers BEGIN until DML, otherwise releasing
                # the first savepoint commits early. Only used by isolated SQL tests.
                if session.get_bind().dialect.name=='sqlite': session.connection().exec_driver_sql('BEGIN')
                for candidate in rows:
                    old=self._get_row(session,table,candidate)
                    if old is None:
                        fields=list(candidate); columns=','.join('`'+f+'`' for f in fields)
                        params=','.join(':'+f for f in fields)
                        try:
                            with session.begin_nested():
                                inserted=session.execute(text(f'INSERT INTO `{table}` ({columns}) VALUES ({params})'),candidate)
                                if inserted.rowcount!=1: raise ValueError('Unexpected actual inserted count')
                        except IntegrityError as exc:
                            code=exc.orig.args[0] if exc.orig.args else None
                            duplicate=code==1062 or (session.get_bind().dialect.name=='sqlite' and getattr(exc.orig,'sqlite_errorcode',None)==2067)
                            if not duplicate: raise
                            old=self._get_row(session,table,candidate)
                            if old is None: raise ValueError('Concurrent key disappeared') from None
                        else:
                            result['inserted']+=1
                            old=self._get_row(session,table,candidate)
                            if old is None or not equal_business(old,candidate,fields,types):
                                raise ValueError('Inserted value read-back differs')
                            continue
                    if equal_business(old,candidate,list(candidate),types): result['already_equal']+=1
                    else:
                        result['conflicts'].append(dict(table=table,ts_code=candidate['ts_code'],trade_date=candidate['trade_date']))
                        session.rollback()
                        return dict(inserted=0,already_equal=0,conflicts=result['conflicts'])
                session.commit()
            except Exception:
                session.rollback(); raise
        return result

    @staticmethod
    def _save_ledger(path,payload):
        tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        write(tmp,payload,exclusive=True)
        os.replace(tmp,path)

    def import_snapshot(self,snapshot: Path,ledger: Path,batch_size: int=500) -> dict:
        snapshot,ledger=Path(snapshot).resolve(),Path(ledger).resolve()
        candidates=import_candidates(snapshot)  # invalid package must never connect
        if not isinstance(batch_size,int) or isinstance(batch_size,bool) or not 1<=batch_size<=500:
            raise ValueError('Batch size must be 1..500')
        if ledger.is_relative_to(snapshot): raise ValueError('Import ledger must be outside immutable source package')
        baseline=load_baseline(snapshot/'evidence/baseline'); tables=baseline['tables']; codes=baseline['pool']['codes']
        current=self.capture_baseline(codes,tables['start'],tables['end'])
        self._assert_original(tables,current)
        ledger.mkdir(parents=True,exist_ok=True); path=ledger/'ledger.json'; lock=ledger/'import.lock'
        try: fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        except FileExistsError: raise RuntimeError('IMPORT_BUSY: existing lock retained') from None
        try:
            binding=dict(snapshot_sha256=digest(snapshot/'manifest.json'),batch_size=batch_size)
            state=read(path) if path.exists() else dict(binding=binding,batches={})
            if state['binding']!=binding: raise ValueError('Import ledger identity differs')
            for table,rows in candidates.items():
                for offset in range(0,len(rows),batch_size):
                    batch=rows[offset:offset+batch_size]; bid=identity(dict(table=table,rows=batch))
                    if bid in state['batches']: continue  # final read-back still verifies every key
                    try: result=self._insert_batch(table,batch,tables['schema'][table]['types'])
                    except Exception:
                        state['failed']=dict(table=table,batch_id=bid,status='TRANSACTION_FAILED')
                        self._save_ledger(path,state)
                        return dict(db_import_complete=False,failed=state['failed'],model_ready=False)
                    if result['conflicts']:
                        state['failed']=dict(table=table,batch_id=bid,status='SOURCE_CONFLICT',conflicts=result['conflicts'])
                        self._save_ledger(path,state)
                        return dict(db_import_complete=False,failed=state['failed'],model_ready=False)
                    state['batches'][bid]=dict(table=table,keys=[[r['ts_code'],r['trade_date']] for r in batch],**result)
                    state.pop('failed',None); self._save_ledger(path,state)
            if not path.exists(): self._save_ledger(path,state)
            verified=self.verify_import(snapshot,ledger)
            state['verification']=verified; self._save_ledger(path,state)
            return verified
        finally:
            os.close(fd); lock.unlink()

    def verify_import(self,snapshot: Path,ledger: Path) -> dict:
        snapshot,ledger=Path(snapshot),Path(ledger)
        candidates=import_candidates(snapshot); baseline=load_baseline(snapshot/'evidence/baseline')
        tables=baseline['tables']; current=self.capture_baseline(baseline['pool']['codes'],tables['start'],tables['end'])
        self._assert_original(tables,current)
        state=read(ledger/'ledger.json')
        if state['binding']['snapshot_sha256']!=digest(snapshot/'manifest.json'): raise ValueError('Import ledger SHA differs')
        missing=[]; conflicts=[]; counts={}
        for table,rows in candidates.items():
            actual={(r['ts_code'],str(r['trade_date'])):r for r in current[table]}
            for row in rows:
                old=actual.get((row['ts_code'],row['trade_date']))
                if old is None: missing.append([table,row['ts_code'],row['trade_date']])
                elif not equal_business(old,row,list(row),tables['schema'][table]['types']):
                    conflicts.append([table,row['ts_code'],row['trade_date']])
            counts[table]=dict(candidate_count=len(rows),recorded_inserted=sum(b['inserted'] for b in state['batches'].values() if b['table']==table),
                recorded_already_equal=sum(b['already_equal'] for b in state['batches'].values() if b['table']==table))
        return dict(db_import_complete=not missing and not conflicts,original_rows_unchanged=True,
                    counts=counts,missing=missing,conflicts=conflicts,model_ready=False)
