"""Bounded insert receipts tied to the immutable baseline and candidate identity."""
from pathlib import Path
import os
import uuid

from app.market_data.repair_contract import canonical
from app.market_data.forward_snapshot import read,write
from .contracts import identity,iso


class FileReceiptStore:
    def __init__(self,directory):
        self.path=Path(directory)/'import_receipts.json'

    def load(self):
        return read(self.path) if self.path.exists() else None

    def save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        write(temporary,data,exclusive=True)
        os.replace(temporary,self.path)


def insert_candidates(repository,baseline,candidates,receipt_store,on_progress):
    if not set(candidates)<={'stock_daily','stock_daily_basic'}:
        raise ValueError('INVALID_CANDIDATE_TABLE')
    baseline=canonical(baseline); candidates=canonical(candidates)
    codes={r['ts_code'] for r in baseline['stock_basic']}
    start,end=iso(baseline['start']),iso(baseline['end'])
    if any(r['ts_code'] not in codes or not start<=iso(r['trade_date'])<=end
           for rows in candidates.values() for r in rows):
        raise ValueError('CANDIDATE_OUTSIDE_FROZEN_BASELINE')
    binding=dict(baseline_sha256=identity(baseline),candidates_sha256=identity(candidates))
    batches={}
    for table in sorted(candidates):
        rows=candidates[table]
        keys=[(r['ts_code'],r['trade_date']) for r in rows]
        if len(keys)!=len(set(keys)): raise ValueError('DUPLICATE_CANDIDATE_KEY')
        for offset in range(0,len(rows),500):
            batch=rows[offset:offset+500]
            batches[identity(dict(table=table,rows=batch))]=(table,batch)
    state=receipt_store.load()
    if state is None: state=dict(binding=binding,batches={})
    if state.get('binding')!=binding or not isinstance(state.get('batches'),dict):
        raise ValueError('IMPORT_RECEIPT_BINDING_CHANGED')
    for bid,receipt in state['batches'].items():
        if bid not in batches: raise ValueError('UNKNOWN_IMPORT_RECEIPT')
        table,rows=batches[bid]
        keys=[[r['ts_code'],r['trade_date']] for r in rows]
        if (set(receipt)!={'table','keys','inserted','already_equal','conflicts'}
                or receipt['table']!=table or receipt['keys']!=keys or receipt['conflicts']!=[]
                or any(type(receipt[k]) is not int or receipt[k]<0 for k in ('inserted','already_equal'))
                or receipt['inserted']+receipt['already_equal']!=len(rows)):
            raise ValueError('INVALID_IMPORT_RECEIPT')
    # Bind the baseline before the first write, including no-candidate operations.
    receipt_store.save(state)
    for bid,(table,rows) in batches.items():
        if bid in state['batches']: continue
        result=repository.insert_batch(table,rows,baseline['schema'][table])
        if result['conflicts']:
            return dict(db_import_complete=False,original_rows_unchanged=None,missing=[],**result)
        if result['inserted']+result['already_equal']!=len(rows):
            raise ValueError('BATCH_RECEIPT_COUNT_INVALID')
        receipt=dict(table=table,keys=[[r['ts_code'],r['trade_date']] for r in rows],**result)
        state['batches'][bid]=receipt
        receipt_store.save(state)
        on_progress(dict(completed_batches=len(state['batches']),total_batches=len(batches),
            inserted=sum(r['inserted'] for r in state['batches'].values()),
            already_equal=sum(r['already_equal'] for r in state['batches'].values())))
    verified=repository.verify(baseline,candidates)
    return dict(verified,inserted=sum(r['inserted'] for r in state['batches'].values()),
                already_equal=sum(r['already_equal'] for r in state['batches'].values()))
