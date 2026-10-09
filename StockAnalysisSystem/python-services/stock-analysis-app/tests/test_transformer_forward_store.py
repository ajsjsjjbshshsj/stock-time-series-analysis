"""Real durable files, completion clock, receipt integrity and competing publishers."""
import copy
import importlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime

import pandas as pd
import pytest


def calendar():
    opened={'2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09',
            '2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'}
    return dict(source='tushare.trade_cal',exchange='SZSE',start='2026-09-28',end='2026-10-16',
        rows=[dict(cal_date=str(d.date()),is_open=int(str(d.date()) in opened)) for d in pd.date_range('2026-09-28','2026-10-16')])


def at(value): return datetime.fromisoformat(value+'+08:00')


def api(): return importlib.import_module('analysis.transformer_forward_store')


def binding():
    codes=[f'{i:06}.SZ' for i in range(20)]
    return dict(freeze_sha256='f'*64,cutoff='2026-09-30',frozen_at='2026-10-05T15:54:01+08:00',codes=codes,
        models=[dict(seed=s,mode=m,model_sha256=f'{s:064x}') for s in (42,123,2026) for m in ('adjusted','unadjusted_control')])


def payload():
    b=binding()
    return dict(signal_date='2026-10-08',models=[dict(seed=m['seed'],mode=m['mode'],scores=[dict(date='2026-10-08',ts_code=c,
        raw=float(i),nonnegative_variance=float(i),model_sha256=m['model_sha256']) for i,c in enumerate(b['codes'])]) for m in b['models']])


def store(tmp_path): return api().ForwardSignalStore(tmp_path/'forward_signals_test',binding())


def publish(s,p=None,clock=lambda:at('2026-10-08T17:00:00')):
    with s.lock('2026-10-08'):
        attempt=s.new_attempt('2026-10-08')
        return s.publish('2026-10-08',attempt,p or payload(),calendar=calendar(),
                         frozen_at=at(binding()['frozen_at'][:-6]),clock=clock)


def test_complete_payload_precedes_publication_clock_and_verification_is_read_only(tmp_path):
    s=store(tmp_path)
    def clock():
        assert (s.root/'signals/2026-10-08/payload.json').is_file()
        assert (s.root/'signals/2026-10-08/marker.json').is_file()
        return at('2026-10-09T08:59:59')
    result=publish(s,clock=clock)
    assert result['status']=='PROSPECTIVE_CANDIDATE' and result['models']==6
    before={str(p):p.read_bytes() for p in s.root.rglob('*') if p.is_file()}
    assert s.verify('2026-10-08')==result
    assert before=={str(p):p.read_bytes() for p in s.root.rglob('*') if p.is_file()}


def test_crossing_deadline_marks_late_not_start_time(tmp_path):
    result=publish(store(tmp_path),clock=lambda:at('2026-10-09T09:00:00'))
    assert result['status']=='LATE_DIAGNOSTIC' and result['prospective_eligible'] is False


@pytest.mark.parametrize('bad',['missing_seed','duplicate','missing_stock','nan'])
def test_partial_or_malformed_group_never_publishes(tmp_path,bad):
    s=store(tmp_path); p=payload()
    if bad=='missing_seed': p['models'].pop()
    elif bad=='duplicate': p['models'][-1]=copy.deepcopy(p['models'][0])
    elif bad=='missing_stock': p['models'][0]['scores'].pop()
    else: p['models'][0]['scores'][0]['raw']=float('nan')
    with pytest.raises(ValueError): publish(s,p)
    assert not (s.root/'signals/2026-10-08').exists()


@pytest.mark.parametrize('file',['payload.json','marker.json','receipt.json','seal.json'])
def test_changed_attestation_or_payload_fails_closed(tmp_path,file):
    s=store(tmp_path); publish(s)
    path=s.root/'signals/2026-10-08'/file
    body=json.loads(path.read_text(encoding='utf-8'))
    if file=='receipt.json': body['published_at']='2026-10-09T08:00:00+08:00'
    else: body['tampered']=True
    path.write_text(json.dumps(body),encoding='utf-8')
    with pytest.raises(ValueError): s.verify('2026-10-08')


@pytest.mark.parametrize('file',['receipt.json','seal.json'])
def test_marker_without_all_completion_evidence_is_not_valid(tmp_path,file):
    s=store(tmp_path); publish(s)
    (s.root/'signals/2026-10-08'/file).unlink()
    with pytest.raises(ValueError): s.verify('2026-10-08')


def test_completed_date_cannot_be_replaced_or_repredicted(tmp_path):
    s=store(tmp_path); publish(s)
    before={str(p):p.read_bytes() for p in s.root.rglob('*') if p.is_file()}
    with s.lock('2026-10-08'):
        with pytest.raises(ValueError): s.new_attempt('2026-10-08')
    assert before=={str(p):p.read_bytes() for p in s.root.rglob('*') if p.is_file()}


def test_competing_process_cannot_acquire_same_date(tmp_path):
    s=store(tmp_path)
    script="import json,sys; from analysis.transformer_forward_store import ForwardSignalStore; s=ForwardSignalStore(sys.argv[1],json.loads(sys.argv[2]));\ntry:\n with s.lock('2026-10-08'): sys.exit(0)\nexcept RuntimeError: sys.exit(2)"
    with s.lock('2026-10-08'):
        result=subprocess.run([sys.executable,'-c',script,str(s.root),json.dumps(binding())],capture_output=True,text=True)
        assert result.returncode==2,result.stderr
    with s.lock('2026-10-08'): pass


def test_failed_publication_preserves_payload_but_has_no_valid_receipt(tmp_path):
    s=store(tmp_path)
    def failed(): raise RuntimeError('clock failed after payload visibility')
    with pytest.raises(RuntimeError): publish(s,clock=failed)
    assert (s.root/'signals/2026-10-08/payload.json').exists()
    with pytest.raises(ValueError): s.verify('2026-10-08')
    with s.lock('2026-10-08'):
        with pytest.raises(ValueError): s.new_attempt('2026-10-08')
