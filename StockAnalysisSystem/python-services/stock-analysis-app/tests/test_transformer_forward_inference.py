"""Frozen score schema and explicit rejection of restarted history."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api(): return importlib.import_module('analysis.transformer_forward_inference')


def test_full_scores_are_unclassified_until_publication_and_ties_are_stable():
    codes=[f'{i:06}.SZ' for i in range(20)]
    rows=[dict(date='2026-10-08',ts_code=c,raw=-1.,nonnegative_variance=0.,model_sha256='a') for c in codes]
    result=api().validate_scores(rows,codes,'2026-10-08','a')
    assert result['top5']['raw']==codes[:5] and result['top5']['nonnegative_variance']==codes[:5]
    assert 'kind' not in result and 'prospective_eligible' not in result
    rows[0]['raw']=np.nan
    with pytest.raises(ValueError): api().validate_scores(rows,codes,'2026-10-08','a')


def test_compatibility_diagnostic_rejects_restart_after_saved_origin(monkeypatch):
    import analysis.transformer_forward_diagnostic as module
    from sklearn.preprocessing import StandardScaler
    codes={f'{i:06}.SZ':i for i in range(20)}
    metadata=dict(feature_history_start='2026-09-01',stock_history_starts={c:'2026-09-01' for c in codes},
                  stockid2idx=codes,selected_features=['x'])
    monkeypatch.setattr(module,'load_model_preprocessing',lambda *a:(StandardScaler(),metadata))
    panel=pd.DataFrame([dict(ts_code=c,trade_date='2026-09-30') for c in codes])
    monkeypatch.setattr(module,'build_feature_panel',lambda *a,**k:pytest.fail('must reject before engineering'))
    with pytest.raises(ValueError,match='histor|origin|start'):
        module.latest_scores(panel,dict(config={},model_path='unused'),'2026-09-30')


@pytest.mark.parametrize('bad',[None,'missing','hash','date','starts'])
def test_real_frozen_score_requires_complete_proof_and_transforms_once(tmp_path,monkeypatch,bad):
    import torch
    from sklearn.preprocessing import StandardScaler
    from analysis.transformer_features import feature_columns,save_model_preprocessing
    from analysis.transformer_model import MultiHeadStockTransformer
    from analysis.transformer_forward_contracts import frame_hash
    import analysis.transformer_forward_diagnostic as diagnostic
    columns=feature_columns('158+39')
    mapping={f'{i:06}.SZ':i for i in range(20)}
    dates=['2026-09-28','2026-09-29','2026-09-30','2026-10-08']
    panel=pd.DataFrame([dict(ts_code=c,trade_date=d,open=10.,high=12.,low=9.,close=11.,vol=100.,amount=1000.) for c in mapping for d in dates])
    config=dict(feature_num='158+39',sequence_length=2,d_model=8,nhead=2,num_layers=1,
                dim_feedforward=16,dropout=0.,use_multi_head=True,score_adjustment_policy='nonnegative_variance')
    scaler=StandardScaler().fit(pd.DataFrame(np.array([[0.]*203,[2.]*203]),columns=columns))
    path=tmp_path/'best_model.pth'
    torch.save(MultiHeadStockTransformer(203,config,20).state_dict(),path)
    save_model_preprocessing(path,scaler,columns,columns,mapping,config,dates[0],{c:dates[0] for c in mapping})
    record=dict(config=config,model_path=str(path))
    proof=dict(signal_date=dates[-1],codes=sorted(mapping),history_starts={c:dates[0] for c in mapping},
               dates=dates,model_panel_sha256=frame_hash(panel))
    calls=[]
    def build(observed,cfg,ids,**kwargs):
        assert kwargs['include_labels'] is False
        calls.append('build')
        raw=pd.DataFrame(np.ones((80,203)),columns=columns)
        raw['日期']=pd.to_datetime(observed.trade_date)
        raw['股票代码']=observed.ts_code
        raw['instrument']=raw['股票代码'].map(ids)
        raw['label']=np.nan
        return raw,columns,ids,'unused'
    monkeypatch.setattr(diagnostic,'build_feature_panel',build)
    original=StandardScaler.transform
    def transform(self,*args,**kwargs):
        calls.append('transform'); return original(self,*args,**kwargs)
    monkeypatch.setattr(StandardScaler,'transform',transform)
    monkeypatch.setattr(StandardScaler,'fit',lambda *a,**kw:pytest.fail('inference must not fit'))
    if bad=='missing': panel=panel[panel.trade_date!='2026-09-29']
    elif bad=='hash': proof['model_panel_sha256']='bad'
    elif bad=='date': proof['signal_date']='2026-09-30'
    elif bad=='starts': proof['history_starts']['000000.SZ']='2026-09-29'
    if bad:
        with pytest.raises(ValueError): api().score_session(panel,record,dates[-1],proof)
        assert calls==[]
    else:
        rows=api().score_session(panel,record,dates[-1],proof)
        assert len(rows)==20 and all(r['date']==dates[-1] for r in rows)
        assert calls==['build','transform']
