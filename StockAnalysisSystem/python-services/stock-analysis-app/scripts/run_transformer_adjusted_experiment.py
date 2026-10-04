"""Real offline comparison; explicit paths, no database writes or deployment.

Example (run from stock-analysis-app): python scripts/run_transformer_adjusted_experiment.py
--stage collect --source models/transformer/pilot_20261004_1814/snapshot.parquet
--output models/transformer/adjusted_experiment_20261004

Use collect, run and verify separately. Models never overwrite old directories.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

APP=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(APP))
os.environ.setdefault('MPLBACKEND','Agg')
os.environ.setdefault('OMP_NUM_THREADS','8')

import pandas as pd
from analysis.transformer_experiment import load_source,sha256,write_json,evaluate_month,summarize,split_month
from data_processor.adjusted_market_panel import build_model_panel

MONTHS=('2026-06','2026-07','2026-08','2026-09')
MODES=('adjusted','unadjusted_control')


def validate_paths(source,output):
    source,output=Path(source).resolve(),Path(output).resolve()
    allowed=APP/'models/transformer'
    if output.parent!=allowed or not output.name.startswith('adjusted_experiment_'):
        raise ValueError('Output must be a new models/transformer/adjusted_experiment_* directory')
    if source.is_relative_to(output):
        raise ValueError('Source must be outside the new output directory')
    return source,output


def collect(source,output):
    panel,manifest=load_source(source)
    output.mkdir(parents=True,exist_ok=True)
    identity=output/'source_identity.json'
    expected=dict(source=str(source),snapshot_sha256=manifest['snapshot_sha256'])
    if identity.exists() and json.loads(identity.read_text(encoding='utf-8'))!=expected:
        raise ValueError('Resume source snapshot mismatch')
    write_json(identity,expected)
    if (output/'factors.parquet').exists():
        factor_manifest=json.loads((output/'factor_manifest.json').read_text(encoding='utf-8'))
        if sha256(output/'factors.parquet')!=factor_manifest['factor_sha256']:
            raise ValueError('Factor snapshot hash mismatch')
        build_model_panel(panel,pd.read_parquet(output/'factors.parquet'))
        print('FACTOR_SNAPSHOT_REUSED',flush=True)
        return
    # Network acquisition stays in python-collector; analysis owns no provider API.
    collector=APP.parent/'python-collector'
    sys.path.insert(0,str(collector))
    import tushare as ts
    from app.config import TUSHARE_TOKEN,COLLECTION_CONFIG
    from app.market_data.factor_snapshot import collect_factor_snapshot
    api=ts.pro_api(TUSHARE_TOKEN,timeout=20)
    started=time.perf_counter()
    try:
        factors=collect_factor_snapshot(api,panel,output/'factor_shards',
            interval=COLLECTION_CONFIG['request_interval'])
    except Exception as error:
        raise RuntimeError(str(error).replace(TUSHARE_TOKEN,'[REDACTED]')) from None
    # Validate full coverage before publishing a completed snapshot.
    build_model_panel(panel,factors)
    factors.to_parquet(output/'factors.parquet',index=False)
    write_json(output/'factor_manifest.json',dict(source='tushare.adj_factor',
        factor_sha256=sha256(output/'factors.parquet'),rows=len(factors),stocks=factors.ts_code.nunique(),
        first_date=str(factors.trade_date.min().date()),last_date=str(factors.trade_date.max().date()),
        volume_unit='lots',amount_unit='thousand_CNY',collection_seconds=time.perf_counter()-started))
    print(f'FACTOR_COLLECTION_COMPLETE rows={len(factors)} stocks={factors.ts_code.nunique()}',flush=True)


def run(source,output):
    import torch
    from analysis.transformer_config import TRANSFORMER_CONFIG
    from analysis.transformer_trainer import compute_and_save_features,run_transformer_training,predict_top_stocks_transformer
    if not torch.cuda.is_available():
        raise RuntimeError('This approved experiment requires CUDA')
    panel,manifest=load_source(source)
    source_identity=json.loads((output/'source_identity.json').read_text(encoding='utf-8'))
    if source_identity!=dict(source=str(source),snapshot_sha256=manifest['snapshot_sha256']):
        raise ValueError('Source identity mismatch')
    factors=pd.read_parquet(output/'factors.parquet')
    factor_manifest=json.loads((output/'factor_manifest.json').read_text(encoding='utf-8'))
    factor_hash=sha256(output/'factors.parquet')
    if factor_hash!=factor_manifest['factor_sha256']:
        raise ValueError('Factor snapshot hash mismatch')
    old_path=output/'old_artifact_hashes_before.json'
    if not old_path.exists():
        old={str(path.relative_to(APP/'models')):sha256(path) for path in (APP/'models').rglob('*')
             if path.is_file() and not path.is_relative_to(output)}
        write_json(old_path,old)
    torch.set_num_threads(min(8,os.cpu_count() or 1))
    adjusted,adjusted_contract=build_model_panel(panel,factors,factor_snapshot_sha256=factor_hash)
    results=[]
    for mode in MODES:
        model_panel,contract=build_model_panel(panel,factors,mode=mode,factor_snapshot_sha256=factor_hash)
        model_panel.to_parquet(output/f'{mode}_panel.parquet',index=False)
        for month in MONTHS:
            folder=output/mode/month
            done=folder/'result.json'
            if done.exists():
                record=json.loads(done.read_text(encoding='utf-8'))
                if record['config']['market_preprocessing']!=contract:
                    raise ValueError('Completed fold preprocessing mismatch')
                results.append(record)
                print(f'FOLD_RESUMED mode={mode} month={month}',flush=True)
                continue
            if folder.exists():
                raise RuntimeError(f'Partial fold preserved at {folder}; use a fresh output for retraining')
            folder.mkdir(parents=True)
            training,_=split_month(model_panel,month)
            if training.empty:
                raise ValueError('No pre-evaluation training history')
            config=dict(TRANSFORMER_CONFIG,output_dir=str(folder/'model'),num_epochs=30,batch_size=8,
                early_stopping_patience=5,early_stopping_min_delta=1e-6,market_preprocessing=contract,
                use_probe_selection=True,probe_selected_features_path=None,seed=42)
            print(f'FOLD_START mode={mode} month={month} train_end={training.trade_date.max().date()}',flush=True)
            started=time.perf_counter()
            feature_path,_,_=compute_and_save_features(training,config=config,use_parallel=True,n_workers=4)
            trained=run_transformer_training(feature_path=feature_path,config=config)
            torch.cuda.synchronize()
            training_seconds=time.perf_counter()-started
            rows=evaluate_month(model_panel,trained,month,benchmark_panel=adjusted)
            write_json(folder/'daily_metrics.json',rows)
            record=dict(trained,mode=mode,month=month,training_seconds=training_seconds,
                train_end=str(training.trade_date.max().date()),summary=summarize(rows),
                usage='known regression comparison' if month=='2026-09' else 'historical diagnostic fold')
            if month=='2026-09':
                forecast=predict_top_stocks_transformer(panel_df=model_panel,model_path=trained['model_path'],
                    config=trained['config'],top_k=5)
                if forecast is None or len(forecast)!=5:
                    raise ValueError('Latest prediction unavailable')
                forecast.to_csv(folder/'latest_prediction.csv',index=False,encoding='utf-8-sig')
                record['latest_inference_date']=str(model_panel.trade_date.max().date())
            write_json(done,record)
            results.append(record)
            print('FOLD_COMPLETE',json.dumps(dict(mode=mode,month=month,epochs=trained['epochs_completed'],
                best_epoch=trained['best_epoch'],summary=record['summary']),ensure_ascii=False),flush=True)
    aggregate={}
    for mode in MODES:
        rows=[]
        for month in MONTHS[:3]:
            rows.extend(json.loads((output/mode/month/'daily_metrics.json').read_text(encoding='utf-8')))
        aggregate[mode]=summarize(rows)
    report=dict(gpu=torch.cuda.get_device_name(0),source_sha256=manifest['snapshot_sha256'],factor_sha256=factor_hash,
        rows=len(panel),stocks=panel.ts_code.nunique(),rolling_months=list(MONTHS[:3]),
        rolling_aggregate=aggregate,folds=results,latest_date=str(panel.trade_date.max().date()),
        deployment='offline only; no registry/database writes',
        limitations=['fixed complete-history stock pool has selection bias','historical vendor snapshots may be revised',
            'Sep already inspected; regression only','overlapping adjusted label returns are not cumulative portfolio returns',
            'no fees, limit-up/down execution or capital simulation',
            'compare common_adjusted_label metrics across modes; their own training label returns differ'])
    write_json(output/'report.json',report)
    verify(source,output)
    print('EXPERIMENT_COMPLETE',str(output/'report.json'),flush=True)


def verify(source,output):
    import numpy as np
    from analysis.transformer_features import load_model_preprocessing
    source_panel,source_manifest=load_source(source)
    report=json.loads((output/'report.json').read_text(encoding='utf-8'))
    if report['source_sha256']!=source_manifest['snapshot_sha256'] or sha256(output/'factors.parquet')!=report['factor_sha256']:
        raise ValueError('Report source/factor hash mismatch')
    for record in report['folds']:
        load_model_preprocessing(record['model_path'],record['config'])
        if pd.Timestamp(record['train_end'])>=pd.Period(record['month'],freq='M').start_time:
            raise ValueError('Training leaked into evaluation month')
        if not 1<=record['epochs_completed']<=30 or not 1<=record['best_epoch']<=record['epochs_completed']:
            raise ValueError('Invalid best epoch')
        rows=json.loads((output/record['mode']/record['month']/'daily_metrics.json').read_text(encoding='utf-8'))
        if record['summary']!=summarize(rows) or any(row['stocks']!=source_panel.ts_code.nunique() for row in rows):
            raise ValueError('Report metrics/stock coverage mismatch')
    if len(report['folds'])!=8:
        raise ValueError('Incomplete controlled experiment')
    for mode in MODES:
        record=next(r for r in report['folds'] if r['mode']==mode and r['month']=='2026-09')
        forecast=pd.read_csv(output/mode/'2026-09/latest_prediction.csv')
        if len(forecast)!=5 or not np.isfinite(forecast['预测分数']).all() or record['latest_inference_date']!=report['latest_date']:
            raise ValueError('Invalid latest forecast')
    old=json.loads((output/'old_artifact_hashes_before.json').read_text(encoding='utf-8'))
    changed=[name for name,digest in old.items() if not (APP/'models'/name).is_file() or sha256(APP/'models'/name)!=digest]
    if changed:
        raise ValueError(f'Old artifact changed: {changed}')
    write_json(output/'verification.json',dict(status='PASS',old_files_checked=len(old),changed=[],folds=8,
        source_sha256=report['source_sha256'],factor_sha256=report['factor_sha256']))
    print(f'VERIFIED folds=8 old_files_unchanged={len(old)} latest={report["latest_date"]}',flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['collect','run','verify'],required=True)
    parser.add_argument('--source',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    source,output=validate_paths(args.source,args.output)
    {'collect':collect,'run':run,'verify':verify}[args.stage](source,output)


if __name__=='__main__':
    main()
