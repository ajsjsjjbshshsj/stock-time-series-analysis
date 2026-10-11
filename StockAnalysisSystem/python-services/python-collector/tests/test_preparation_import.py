import importlib
from copy import deepcopy

import pytest


def api():
    try: return importlib.import_module('app.preparation.importer')
    except ModuleNotFoundError: pytest.fail('preparation importer not implemented')


class Receipts:
    def __init__(self): self.data=None
    def load(self): return deepcopy(self.data)
    def save(self,data): self.data=deepcopy(data)


class Repository:
    def __init__(self): self.scans=0; self.batches=0; self.present=set()
    def capture(self,codes,start,end):
        self.scans+=1
        return dict(start=start,end=end,stock_basic=[dict(ts_code=f'{i:06d}.SZ') for i in range(1,1501)],
            stock_daily=[],stock_daily_basic=[],schema={'stock_daily_basic':dict(types={})})
    def insert_batch(self,table,rows,schema):
        self.batches+=1; inserted=0
        for row in rows:
            key=row['ts_code'],row['trade_date']
            if key not in self.present: inserted+=1; self.present.add(key)
        return dict(inserted=inserted,already_equal=len(rows)-inserted,conflicts=[])
    def verify(self,baseline,candidates):
        self.capture(['000001.SZ'],baseline['start'],baseline['end'])
        return dict(db_import_complete=True,original_rows_unchanged=True,missing=[],conflicts=[])


def candidates():
    return {'stock_daily_basic':[dict(ts_code=f'{i:06d}.SZ',trade_date='2026-09-28') for i in range(1,1501)]}


def test_full_baseline_only_at_beginning_and_final_verification():
    repo=Repository(); receipts=Receipts()
    baseline=repo.capture(['000001.SZ'],'2026-09-28','2026-09-30')
    result=api().insert_candidates(repo,baseline,candidates(),receipts,lambda progress:None)
    assert result['db_import_complete'] is True
    assert result['inserted']==1500
    assert repo.scans==2
    assert repo.batches==3


def test_commit_before_receipt_crash_restores_as_equal_not_new_inserts():
    class InterruptedReceipts(Receipts):
        def __init__(self): super().__init__(); self.crashed=False
        def save(self,data):
            if data['batches'] and not self.crashed:
                self.crashed=True
                raise RuntimeError('commit completed; receipt not persisted')
            super().save(data)
    repo=Repository(); receipts=InterruptedReceipts()
    baseline=repo.capture(['000001.SZ'],'2026-09-28','2026-09-30')
    with pytest.raises(RuntimeError): api().insert_candidates(repo,baseline,candidates(),receipts,lambda progress:None)
    result=api().insert_candidates(repo,baseline,candidates(),receipts,lambda progress:None)
    assert result['inserted']==1000
    assert result['already_equal']==500
    assert result['db_import_complete'] is True


def test_changed_baseline_or_candidates_refused_on_resume():
    repo=Repository(); receipts=Receipts()
    baseline=repo.capture(['000001.SZ'],'2026-09-28','2026-09-30')
    api().insert_candidates(repo,baseline,candidates(),receipts,lambda progress:None)
    changed=dict(baseline,end='2026-10-01')
    with pytest.raises(ValueError): api().insert_candidates(repo,changed,candidates(),receipts,lambda progress:None)


def test_candidates_outside_frozen_baseline_refused_before_write():
    repo=Repository(); receipts=Receipts()
    baseline=repo.capture(['000001.SZ'],'2026-09-28','2026-09-30')
    with pytest.raises(ValueError):
        api().insert_candidates(repo,baseline,{'stock_daily_basic':[dict(ts_code='999999.SZ',trade_date='2026-09-28')]},receipts,lambda progress:None)
    assert repo.batches==0
