"""One-shot runner with real files/models; remote acquisition is the only fake."""
import copy
from datetime import datetime
import importlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch
from sklearn.preprocessing import StandardScaler

from analysis.transformer_experiment import sha256,write_json
from analysis.transformer_features import feature_columns,save_model_preprocessing
from analysis.transformer_forward_contracts import frame_hash
from analysis.transformer_forward_freeze import frozen_rules
from analysis.transformer_model import MultiHeadStockTransformer
from data_processor.adjusted_market_panel import build_model_panel


def api(): return importlib.import_module('scripts.run_transformer_forward_signals')


def at(value): return datetime.fromisoformat(value+'+08:00')


@pytest.fixture
def environment(tmp_path,monkeypatch):
    module=api()
    app=tmp_path/'app'; (app/'models/transformer').mkdir(parents=True)
    freeze=app/'models/transformer/forward_freeze_20261005'; freeze.mkdir()
    codes=[f'{i:06}.SZ' for i in range(20)]
    dates=['2026-09-28','2026-09-29','2026-09-30']
    daily=pd.DataFrame([dict(ts_code=c,trade_date=d,open=10.,high=12.,low=9.,close=11.,vol=100.,amount=1000.) for c in codes for d in dates])
    factors=daily[['ts_code','trade_date']].assign(adj_factor=2.)
    factors.to_parquet(freeze/'training_factors.parquet',index=False)
    factor_sha=sha256(freeze/'training_factors.parquet')
    columns=feature_columns('158+39'); mapping={c:i for i,c in enumerate(codes)}
    models=[]
    for seed in (42,123,2026):
        for mode in ('adjusted','unadjusted_control'):
            folder=freeze/f'{seed}_{mode}';folder.mkdir()
            _,contract=build_model_panel(daily,factors,mode=mode,factor_snapshot_sha256=factor_sha)
            config=dict(feature_num='158+39',sequence_length=2,d_model=8,nhead=2,num_layers=1,
                        dim_feedforward=16,dropout=0.,use_multi_head=True,score_adjustment_policy='nonnegative_variance',market_preprocessing=contract)
            torch.manual_seed(seed+(10000 if mode=='adjusted' else 0))
            path=folder/'best_model.pth'
            torch.save(MultiHeadStockTransformer(203,config,20).state_dict(),path)
            scaler=StandardScaler().fit(pd.DataFrame(np.array([[0.]*203,[2.]*203]),columns=columns))
            scale_path=save_model_preprocessing(path,scaler,columns,columns,mapping,config,dates[0],{c:dates[0] for c in codes})
            models.append(dict(seed=seed,mode=mode,model_path=str(path),scaler_path=scale_path,config=config))
    frozen=dict(daily=daily,factors=factors,models=models,metadata=dict(stockid2idx=mapping,feature_history_start=dates[0],
                stock_history_starts={c:dates[0] for c in codes}),
        binding=dict(freeze_dir=str(freeze),freeze_sha256='f'*64,cutoff='2026-09-30',frozen_at='2026-10-05T15:54:01+08:00',
            training_factor_sha256=factor_sha,codes=codes,rules=frozen_rules(),
            models=[dict(seed=m['seed'],mode=m['mode'],model_sha256=sha256(m['model_path']),scaler_sha256=sha256(m['scaler_path'])) for m in models]))
    calls=[]
    def collect(stage,request,output):
        calls.append(stage);output=Path(output);output.mkdir(parents=True,exist_ok=True)
        if stage=='calendar':
            opened={'2026-09-28','2026-09-29','2026-09-30','2026-10-08','2026-10-09','2026-10-12','2026-10-13','2026-10-14','2026-10-15','2026-10-16'}
            cal=dict(source='tushare.trade_cal',exchange='SZSE',start=request['start'],end=request['end'],
                rows=[dict(cal_date=str(d.date()),is_open=int(str(d.date()) in opened)) for d in pd.date_range(request['start'],request['end'])])
            write_json(output/'calendar.json',cal)
            write_json(output/'calendar_manifest.json',dict(calendar_sha256=sha256(output/'calendar.json')))
        else:
            new=pd.DataFrame([dict(ts_code=c,trade_date=d,open=11.,high=12.,low=9.,close=11.,vol=100.,amount=1000.) for c in request['codes'] for d in request['dates']])
            new.to_parquet(output/'daily.parquet',index=False)
            new[['ts_code','trade_date']].assign(adj_factor=2.).to_parquet(output/'factors.parquet',index=False)
            manifest=dict(source='tushare',binding=request['binding'],codes=request['codes'],dates=request['dates'],
                volume_unit='lots',amount_unit='thousand_CNY',daily_rows=len(new),factor_rows=len(new),
                daily_sha256=sha256(output/'daily.parquet'),factor_sha256=sha256(output/'factors.parquet'))
            write_json(output/'acquisition_manifest.json',manifest)
    monkeypatch.setattr(module,'APP',app)
    monkeypatch.setattr(module,'load_frozen',lambda p:copy.deepcopy(frozen))
    monkeypatch.setattr(module,'collect',collect)
    monkeypatch.setattr(module,'now',lambda:at('2026-10-08T17:00:00'))
    output=app/'models/transformer/forward_signals_20261008'
    return module,freeze,output,calls,frozen


def test_real_six_model_run_then_read_only_resume_and_verify(environment,monkeypatch):
    module,freeze,output,calls,_=environment
    result=module.run(freeze,output)
    assert result['status']=='PROSPECTIVE_CANDIDATE' and result['models']==6
    assert calls==['calendar','market']
    before={str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(module,'collect',lambda *a:pytest.fail('completed run must not request'))
    monkeypatch.setattr(module,'score_session',lambda *a:pytest.fail('completed run must not infer'))
    assert module.run(freeze,output,'2026-10-08')==result
    assert module.verify(freeze,output,'2026-10-08')==result
    assert before=={str(p):p.read_bytes() for p in output.rglob('*') if p.is_file()}


def test_no_new_completed_session_does_not_collect_market_or_score(environment,monkeypatch):
    module,freeze,output,calls,_=environment
    monkeypatch.setattr(module,'now',lambda:at('2026-10-08T15:59:00'))
    monkeypatch.setattr(module,'score_session',lambda *a:pytest.fail('no new completed session'))
    assert module.run(freeze,output)['status']=='NO_NEW_SESSION'
    assert calls==['calendar']


def test_remote_failure_preserves_stage_without_publishing_or_training(environment,monkeypatch):
    module,freeze,output,_,_=environment
    monkeypatch.setattr(module,'collect',lambda *a:(_ for _ in ()).throw(RuntimeError('ACQUISITION_HOURLY_QUOTA')))
    with pytest.raises(RuntimeError):module.run(freeze,output)
    assert not list(output.glob('signals/*/receipt.json'))


def test_path_overlap_and_changed_source_identity_fail_closed(environment,monkeypatch):
    module,freeze,output,_,frozen=environment
    with pytest.raises(ValueError):module.validate_paths(freeze,freeze/'forward_signals_nested')
    module.run(freeze,output)
    changed=copy.deepcopy(frozen);changed['binding']['freeze_sha256']='0'*64
    monkeypatch.setattr(module,'load_frozen',lambda p:changed)
    with pytest.raises(ValueError):module.run(freeze,output,'2026-10-08')


def test_completed_acquisition_or_calendar_change_rejected(environment):
    module,freeze,output,_,_=environment
    module.run(freeze,output)
    paths=list(output.glob('acquisitions/*/daily.parquet'))
    assert len(paths)==1
    paths[0].write_bytes(paths[0].read_bytes()+b'changed')
    with pytest.raises(ValueError):module.verify(freeze,output,'2026-10-08')


def test_cli_has_no_generated_time_override():
    with pytest.raises(SystemExit):api().main(['--stage','run','--freeze-dir','x','--output','y','--generated-at','2026-10-08T17:00:00'])


def test_new_session_cannot_revise_already_published_forward_history(environment,monkeypatch):
    module,freeze,output,_,_=environment
    module.run(freeze,output)
    original=module.collect
    monkeypatch.setattr(module,'now',lambda:at('2026-10-09T17:00:00'))
    def revised(stage,request,path):
        original(stage,request,path)
        if stage=='market':
            path=Path(path)
            frame=pd.read_parquet(path/'daily.parquet')
            frame.loc[frame.trade_date=='2026-10-08','open']=10.5
            frame.to_parquet(path/'daily.parquet',index=False)
            manifest=module.read(path/'acquisition_manifest.json')
            manifest['daily_sha256']=sha256(path/'daily.parquet')
            write_json(path/'acquisition_manifest.json',manifest)
    monkeypatch.setattr(module,'collect',revised)
    with pytest.raises(ValueError,match='published|prior|prefix|revis'):
        module.run(freeze,output)
    assert not (output/'signals/2026-10-09').exists()


def test_short_cached_calendar_is_extended_before_market_collection(environment,monkeypatch):
    module,freeze,output,calls,frozen=environment
    module._store(output,frozen,initialize=True)
    short=output/'calendars/short';short.mkdir(parents=True)
    opened={'2026-09-28','2026-09-29','2026-09-30','2026-10-08'}
    body=dict(source='tushare.trade_cal',exchange='SZSE',start='2026-09-28',end='2026-10-08',
        rows=[dict(cal_date=str(d.date()),is_open=int(str(d.date()) in opened)) for d in pd.date_range('2026-09-28','2026-10-08')])
    write_json(short/'calendar.json',body)
    write_json(short/'calendar_manifest.json',dict(calendar_sha256=sha256(short/'calendar.json')))
    before=(short/'calendar.json').read_bytes()
    calendar,path=module._calendar(output,frozen)
    assert path!=short and calendar['end']>'2026-10-08'
    assert calls==['calendar'] and (short/'calendar.json').read_bytes()==before


def test_alternate_store_rejected_before_acquisition_or_inference(environment,monkeypatch):
    module,freeze,output,calls,_=environment
    monkeypatch.setattr(module,'collect',lambda *a:pytest.fail('alternate output must not acquire'))
    monkeypatch.setattr(module,'score_session',lambda *a:pytest.fail('alternate output must not score'))
    with pytest.raises(ValueError):module.run(freeze,output.with_name('forward_signals_other'),'2026-10-08')
    assert calls==[]


def test_default_completed_run_does_not_even_create_a_lock(environment,monkeypatch):
    module,freeze,output,_,_=environment
    result=module.run(freeze,output)
    monkeypatch.setattr(module.ForwardSignalStore,'lock',lambda *a:pytest.fail('default completed resume must be read-only'))
    assert module.run(freeze,output)==result


def test_cli_safe_failure_includes_stage_and_never_provider_secret(environment,monkeypatch,capsys):
    module,freeze,output,_,_=environment
    monkeypatch.setattr(module,'collect',lambda *a:(_ for _ in ()).throw(RuntimeError('secret-token-value')))
    assert module.main(['--stage','run','--freeze-dir',str(freeze),'--output',str(output)])==1
    captured=capsys.readouterr()
    assert 'secret-token-value' not in captured.err+captured.out
    assert 'calendar_acquisition' in captured.err and 'retryable=' in captured.err
