"""Evaluate the experiment contract without real network or GPU dependencies."""
import numpy as np
import pandas as pd
import pytest


def test_month_fold_never_trains_on_evaluation_dates():
    from analysis.transformer_experiment import split_month
    panel=pd.DataFrame({'trade_date':pd.to_datetime(['2026-05-29','2026-06-01','2026-06-30','2026-07-01'])})
    train,test=split_month(panel,'2026-06')
    assert list(train.trade_date.dt.strftime('%Y-%m-%d'))==['2026-05-29']
    assert list(test.trade_date.dt.strftime('%Y-%m-%d'))==['2026-06-01','2026-06-30']


def test_metrics_distinguish_raw_and_adjusted_top5():
    from analysis.transformer_experiment import daily_metrics
    scores=np.arange(10,dtype=float)
    adjusted=-scores
    returns=np.arange(10)/100
    result=daily_metrics(scores,adjusted,returns,-scores,k=5)
    assert result['raw_top5_return']==pytest.approx(.07)
    assert result['adjusted_top5_return']==pytest.approx(.02)
    assert result['equal_weight_return']==pytest.approx(.045)
    assert result['momentum_top5_return']==pytest.approx(.02)
    assert result['raw_excess']==pytest.approx(.025)
    assert result['rank_ic']==pytest.approx(1.)


def test_degenerate_ic_is_none_not_nan():
    from analysis.transformer_experiment import daily_metrics
    result=daily_metrics(np.ones(10),np.ones(10),np.zeros(10),np.zeros(10))
    assert result['rank_ic'] is None


def test_metrics_reject_invalid_observations():
    from analysis.transformer_experiment import daily_metrics
    with pytest.raises(ValueError,match='finite|shape'):
        daily_metrics(np.array([np.nan]*10),np.ones(10),np.zeros(10),np.zeros(10))


def test_source_snapshot_hash_is_enforced(tmp_path):
    import json
    from analysis.transformer_experiment import load_source
    path=tmp_path/'snapshot.parquet'
    pd.DataFrame({'ts_code':['000001.SZ'],'trade_date':pd.to_datetime(['2026-09-30'])}).to_parquet(path)
    path.with_name('collection_manifest.json').write_text(json.dumps({'snapshot_sha256':'invalid'}))
    with pytest.raises(ValueError,match='hash|snapshot'):
        load_source(path)


def test_adjusted_scores_match_production_three_head_variance():
    import torch
    from analysis.transformer_experiment import adjusted_head_scores
    outputs={key:torch.tensor([values]) for key,values in {
        'ranking':[2.,4.],'regression':[0.,1.],
        'classification':[1.,0.],'direction':[0.,1.]}.items()}
    raw,adjusted=adjusted_head_scores(outputs)
    np.testing.assert_allclose(raw,[2.,4.])
    np.testing.assert_allclose(adjusted,[14/9,28/9],rtol=1e-6)


def test_runner_help_does_not_request_api_or_train():
    import subprocess,sys
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'scripts/run_transformer_adjusted_experiment.py'
    result=subprocess.run([sys.executable,str(script),'--help'],capture_output=True,text=True)
    assert result.returncode==0
    assert '--stage' in result.stdout and '--source' in result.stdout and '--output' in result.stdout


def runner_module():
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'scripts/run_transformer_adjusted_experiment.py'
    spec=importlib.util.spec_from_file_location('adjusted_runner_test',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('bad',['identity','config','model_path','train_end'])
def test_resume_rejects_wrong_fold_or_training_configuration(tmp_path,bad):
    runner=runner_module()
    folder=tmp_path/'adjusted/2026-06'
    config={'market_preprocessing':{'mode':'adjusted'},'num_epochs':30}
    record=dict(mode='adjusted',month='2026-06',config=config.copy(),train_end='2026-05-29',
        model_path=str(folder/'model/best_model.pth'),scaler_path=str(folder/'model/best_model_scaler.pkl'),
        best_epoch=1,epochs_completed=2)
    if bad=='identity':
        record['month']='2026-07'
    elif bad=='config':
        record['config']=dict(config,num_epochs=3)
    elif bad=='model_path':
        record['model_path']=str(tmp_path/'another/best_model.pth')
    else:
        record['train_end']='2026-05-28'
    with pytest.raises(ValueError,match='fold|Fold|config|path'):
        runner.validate_fold_identity(record,'adjusted','2026-06',folder,config,'2026-05-29')


def test_verifier_rejects_duplicated_months_not_just_fold_count(tmp_path,monkeypatch):
    import json
    runner=runner_module()
    monkeypatch.setattr(runner,'load_source',lambda path:(pd.DataFrame(),{'snapshot_sha256':'hash'}))
    monkeypatch.setattr(runner,'sha256',lambda path:'hash')
    rows=[dict(mode=mode,month=month) for mode in runner.MODES for month in runner.MONTHS]
    rows[0]=dict(rows[1])  # Eight records, but one missing month and one duplicate.
    report=dict(source_sha256='hash',factor_sha256='hash',folds=rows)
    (tmp_path/'report.json').write_text(json.dumps(report),encoding='utf-8')
    with pytest.raises(ValueError,match='fold|Fold'):
        runner.verify(tmp_path/'source.parquet',tmp_path)


def test_expected_mature_dates_include_all_sessions_and_exclude_label_tail():
    runner=runner_module()
    dates=pd.bdate_range('2026-09-01',periods=12)
    panel=pd.DataFrame({'ts_code':['000001.SZ']*12,'trade_date':dates})
    assert runner.expected_evaluation_dates(panel,'2026-09',sequence_length=2)==[
        '2026-09-02','2026-09-03','2026-09-04','2026-09-07','2026-09-08','2026-09-09']
