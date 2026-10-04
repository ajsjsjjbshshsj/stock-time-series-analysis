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
