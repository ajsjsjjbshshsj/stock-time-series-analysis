"""Offline controlled evaluation, deliberately separate from API model registry."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from analysis.transformer_scoring import adjust_scores, score_policy, POLICIES


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str,allow_nan=False),encoding='utf-8')


def load_source(path):
    path=Path(path)
    manifest=json.loads(path.with_name('collection_manifest.json').read_text(encoding='utf-8'))
    if sha256(path)!=manifest['snapshot_sha256']:
        raise ValueError('Source snapshot hash mismatch')
    panel=pd.read_parquet(path)
    panel['trade_date']=pd.to_datetime(panel.trade_date)
    return panel,manifest


def split_month(panel,month):
    first=pd.Period(month,freq='M').start_time
    following=first+pd.offsets.MonthBegin(1)
    dates=pd.to_datetime(panel.trade_date)
    return panel[dates<first].copy(),panel[(dates>=first)&(dates<following)].copy()


def daily_metrics(scores,adjusted_scores,returns,momentum,k=5):
    vectors=[np.asarray(x,dtype=float) for x in (scores,adjusted_scores,returns,momentum)]
    if (not all(x.ndim==1 and x.shape==vectors[0].shape and np.isfinite(x).all() for x in vectors)
            or len(vectors[0])<k):
        raise ValueError('Metric vectors must have finite matching shapes')
    raw,adjusted,labels,mom=vectors
    # Stable ties make baseline/repeats reproducible; no random portfolio metric.
    top=lambda x:np.argsort(-x,kind='stable')[:k]
    raw_return=float(labels[top(raw)].mean())
    adjusted_return=float(labels[top(adjusted)].mean())
    equal=float(labels.mean())
    ic=pd.Series(raw).corr(pd.Series(labels),method='spearman') if raw.std()>0 and labels.std()>0 else np.nan
    return dict(raw_top5_return=raw_return,adjusted_top5_return=adjusted_return,equal_weight_return=equal,
        momentum_top5_return=float(labels[top(mom)].mean()),raw_excess=raw_return-equal,
        adjusted_excess=adjusted_return-equal,rank_ic=float(ic) if np.isfinite(ic) else None)


def adjusted_head_scores(outputs, policy='legacy_variance'):
    """Mirror existing production uncertainty adjustment, not a new algorithm."""
    arrays={name:value.detach().reshape(-1).cpu().numpy() for name,value in outputs.items()}
    adjusted,_=adjust_scores(*(arrays[name] for name in ('ranking','regression','classification','direction')),policy=policy)
    return arrays['ranking'],adjusted


def evaluate_month(panel,result,month,benchmark_panel=None,scoring_policies=None):
    import torch
    from analysis.transformer_features import load_model_preprocessing,build_feature_panel,prepare_inference_data
    from analysis.transformer_utils import create_ranking_dataset_vectorized
    from analysis.transformer_model import MultiHeadStockTransformer
    config=result['config']
    if scoring_policies is not None and (len(set(scoring_policies))!=len(scoring_policies)
            or any(policy not in POLICIES for policy in scoring_policies)):
        raise ValueError('Invalid evaluation scoring policies')
    scaler,metadata=load_model_preprocessing(result['model_path'],config)
    first=pd.Period(month,freq='M').start_time
    following=first+pd.offsets.MonthBegin(1)
    config=dict(config,feature_start_date=metadata['feature_history_start'],stock_history_starts=metadata['stock_history_starts'])
    raw,columns,_,_=build_feature_panel(panel,config,metadata['stockid2idx'])
    benchmark=None
    if benchmark_panel is not None:
        benchmark=benchmark_panel.sort_values(['ts_code','trade_date']).copy()
        grouped=benchmark.groupby('ts_code').open
        benchmark['label']=grouped.shift(-5)/grouped.shift(-1)-1
        benchmark=benchmark.set_index(['ts_code','trade_date']).label
    context=prepare_inference_data(raw,columns,scaler)
    context.loc[context['日期']>=following,'label']=np.nan
    # Features include historical context; only mature evaluation labels survive.
    dataset=create_ranking_dataset_vectorized(context,metadata['selected_features'],config['sequence_length'],min_window_end_date=first)
    dates=sorted(raw.loc[(raw['日期']>=first)&(raw['日期']<following)&raw.label.notna(),'日期'].unique())
    if not dates or len(dataset[0])!=len(dates):
        raise ValueError('Mature holdout dates/sequence coverage mismatch')
    device=torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model=MultiHeadStockTransformer(len(metadata['selected_features']),config,len(metadata['stockid2idx'])).to(device)
    model.load_state_dict(torch.load(result['model_path'],map_location=device,weights_only=True))
    model.eval()
    rows=[]
    with torch.no_grad():
        for date,sequence,targets,ids in zip(dates,dataset[0],dataset[1],dataset[3]):
            if len(ids)!=len(metadata['stockid2idx']):
                raise ValueError('Incomplete fixed-universe holdout date')
            outputs=model(torch.from_numpy(sequence).unsqueeze(0).to(device),return_all_heads=True)
            scores,adjusted=adjusted_head_scores(outputs,score_policy(config))
            momentum=raw[raw['日期']==date].set_index('instrument').loc[ids,'return_5'].to_numpy()
            row=dict(date=str(pd.Timestamp(date).date()),stocks=len(ids),**daily_metrics(scores,adjusted,targets,momentum))
            if benchmark is not None:
                inverse={index:code for code,index in metadata['stockid2idx'].items()}
                truth=np.array([benchmark.loc[(inverse[index],date)] for index in ids])
                common=daily_metrics(scores,adjusted,truth,momentum)
                row.update({f'common_adjusted_label_{key}':value for key,value in common.items()})
            if scoring_policies is not None:
                truth=truth if benchmark is not None else targets
                raw_top=set(np.argsort(-scores,kind='stable')[:5])
                for policy in scoring_policies:
                    _,policy_scores=adjusted_head_scores(outputs,policy)
                    metric=daily_metrics(scores,policy_scores,truth,momentum)
                    prefix=f'policy_{policy}_'
                    row[prefix+'top5_return']=metric['adjusted_top5_return']
                    row[prefix+'excess']=metric['adjusted_excess']
                    row[prefix+'top5_changed']=int(raw_top!=set(np.argsort(-policy_scores,kind='stable')[:5]))
            rows.append(row)
    return rows


def summarize(rows):
    keys=[key for key in rows[0] if key not in ('date','stocks')]
    result=dict(days=len(rows),first_date=rows[0]['date'],last_date=rows[-1]['date'])
    for key in keys:
        values=[row[key] for row in rows if row[key] is not None]
        result[key]=float(np.mean(values)) if values else None
    return result
