"""Date-first probe validation and deterministic optional early stopping."""
import numpy as np
import pandas as pd
import pytest


def test_stock_sorted_probe_is_split_by_date_and_targets_are_purged():
    from data_processor.probe_selection import split_probe_data
    dates = pd.bdate_range('2024-01-02', periods=20)
    frame = pd.concat([pd.DataFrame({'日期':dates,'label_target_date':dates+pd.offsets.BDay(5),
        'instrument':code,'label':np.arange(20)/100}) for code in range(2)], ignore_index=True)
    train, val = split_probe_data(frame, train_ratio=.75)
    assert train['日期'].max() == dates[9]
    assert val['日期'].min() == dates[15]
    assert len(train)==20 and len(val)==10
    assert train.label_target_date.max()<val['日期'].min()
    assert not set(train['日期']) & set(val['日期'])


@pytest.mark.parametrize('count',[1,5])
def test_probe_rejects_insufficient_purged_history(count):
    from data_processor.probe_selection import split_probe_data
    dates=pd.bdate_range('2024-01-02',periods=count)
    frame=pd.DataFrame({'日期':dates,'label_target_date':dates+pd.offsets.BDay(5),'label':0.})
    with pytest.raises(ValueError,match='probe|Probe|history'):
        split_probe_data(frame)


def test_early_stop_patience_and_best_value():
    from analysis.training_control import EarlyStopping
    state=EarlyStopping(patience=2,min_delta=.01)
    assert state.update(.1)==(True,False)
    assert state.update(.11)==(False,False)
    assert state.update(.105)==(False,True)
    assert state.best == .1


def test_early_stop_improvement_resets_patience():
    from analysis.training_control import EarlyStopping
    state=EarlyStopping(patience=2,min_delta=.01)
    assert state.update(.1)==(True,False)
    assert state.update(.09)==(False,False)
    assert state.update(.2)==(True,False)
    assert state.update(.19)==(False,False)


def test_disabled_early_stop_does_not_stop():
    from analysis.training_control import EarlyStopping
    state=EarlyStopping()
    assert state.update(1)==(True,False)
    for _ in range(10):
        assert state.update(0)==(False,False)


@pytest.mark.parametrize('bad',[np.nan,np.inf,-np.inf])
def test_early_stop_rejects_nonfinite_score(bad):
    from analysis.training_control import EarlyStopping
    with pytest.raises(ValueError,match='finite'):
        EarlyStopping().update(bad)


def test_trainer_stops_and_retains_best_checkpoint(tmp_path,monkeypatch):
    import torch
    from analysis import transformer_trainer as trainer
    dates=pd.bdate_range('2023-01-02',periods=180)
    panel=pd.concat([pd.DataFrame(dict(ts_code=f'{i+1:06d}.SZ',trade_date=dates,
        open=10.+np.arange(180)*.01,high=12.,low=9.,close=10.+np.arange(180)*.01,
        vol=100.,amount=100.)) for i in range(10)],ignore_index=True)
    # Isolate costly epochs only; exercise real preprocessing, loaders and saving.
    def controlled_epoch(model,loader,criterion,optimizer,device,epoch,config):
        with torch.no_grad():
            next(model.parameters()).fill_(epoch+1)
        optimizer.step()
        return 0.,{}
    monkeypatch.setattr(trainer,'train_ranking_model',controlled_epoch)
    from itertools import chain, repeat
    scores=chain([.2,.1],repeat(.05))
    monkeypatch.setattr(trainer,'evaluate_ranking_model',lambda *a,**k: (0.,{'final_score':next(scores)}))
    monkeypatch.setattr(torch.cuda,'is_available',lambda:False)
    import visualization.plotter
    monkeypatch.setattr(visualization.plotter.StockPlotter,'plot_transformer_training_history',lambda *a,**k:None)
    config=dict(trainer.TRANSFORMER_CONFIG,feature_num='39',output_dir=str(tmp_path),
        num_epochs=10,use_probe_selection=False,early_stopping_patience=2,
        early_stopping_min_delta=0.,d_model=16,nhead=2,num_layers=1,dim_feedforward=16)
    result=trainer.run_transformer_training(panel_df=panel,config=config)
    assert len(result['history'])==3
    assert result['best_epoch']==1 and result['stopped_early'] is True
    assert result['best_score']==.2
    state=torch.load(result['model_path'],weights_only=True)
    assert torch.all(next(iter(state.values()))==1.)
    from analysis.transformer_features import load_model_preprocessing
    load_model_preprocessing(result['model_path'],result['config'])
