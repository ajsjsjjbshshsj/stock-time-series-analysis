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
import subprocess
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


def experiment_config(folder,contract):
    from analysis.transformer_config import TRANSFORMER_CONFIG
    return dict(TRANSFORMER_CONFIG,output_dir=str(folder/'model'),num_epochs=30,batch_size=8,
        early_stopping_patience=5,early_stopping_min_delta=1e-6,market_preprocessing=contract,
        use_probe_selection=True,probe_selected_features_path=None,seed=42)


def validate_fold_identity(record,mode,month,folder,config,train_end):
    if (record.get('mode')!=mode or record.get('month')!=month or record.get('config')!=config
            or record.get('train_end')!=str(pd.Timestamp(train_end).date())):
        raise ValueError('Fold identity/config/training cutoff mismatch')
    expected={'model_path':folder/'model/best_model.pth','scaler_path':folder/'model/best_model_scaler.pkl'}
    if any(Path(record.get(key,'')).resolve()!=path.resolve() for key,path in expected.items()):
        raise ValueError('Fold artifact path mismatch')
    if not 1<=record.get('best_epoch',0)<=record.get('epochs_completed',0)<=30:
        raise ValueError('Fold epoch metadata invalid')


def expected_evaluation_dates(panel,month,sequence_length=60):
    """Derive coverage from source observations, not potentially partial metrics."""
    dates=sorted(pd.to_datetime(panel.trade_date).unique())
    first=pd.Period(month,freq='M').start_time
    following=first+pd.offsets.MonthBegin(1)
    return [str(pd.Timestamp(date).date()) for index,date in enumerate(dates)
            if index>=sequence_length-1 and index+5<len(dates) and first<=date<following]


def validate_fold_metrics(record,rows,panel):
    expected=expected_evaluation_dates(panel,record['month'],record['config']['sequence_length'])
    if not expected or [row['date'] for row in rows]!=expected:
        raise ValueError('Fold mature date coverage mismatch')
    if record['summary']!=summarize(rows) or any(row['stocks']!=panel.ts_code.nunique() for row in rows):
        raise ValueError('Fold metric summary/stock coverage mismatch')


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
    # Provider imports/configuration remain wholly in the collector process.
    script=APP.parent/'python-collector/scripts/collect_factor_snapshot.py'
    subprocess.run([sys.executable,str(script),'--source',str(source),'--output',str(output)],check=True)
    build_model_panel(panel,pd.read_parquet(output/'factors.parquet'))


def run(source,output):
    import torch
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
            training,_=split_month(model_panel,month)
            config=experiment_config(folder,contract)
            done=folder/'result.json'
            if done.exists():
                record=json.loads(done.read_text(encoding='utf-8'))
                validate_fold_identity(record,mode,month,folder,config,training.trade_date.max())
                from analysis.transformer_features import load_model_preprocessing
                load_model_preprocessing(record['model_path'],record['config'])
                rows=json.loads((folder/'daily_metrics.json').read_text(encoding='utf-8'))
                validate_fold_metrics(record,rows,panel)
                results.append(record)
                print(f'FOLD_RESUMED mode={mode} month={month}',flush=True)
                continue
            if folder.exists():
                raise RuntimeError(f'Partial fold preserved at {folder}; use a fresh output for retraining')
            folder.mkdir(parents=True)
            if training.empty:
                raise ValueError('No pre-evaluation training history')
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
    identities=[(record.get('mode'),record.get('month')) for record in report['folds']]
    if len(identities)!=8 or set(identities)!={(mode,month) for mode in MODES for month in MONTHS}:
        raise ValueError('Fold identity set incomplete or duplicated')
    factors=pd.read_parquet(output/'factors.parquet')
    contracts={mode:build_model_panel(source_panel,factors,mode=mode,
        factor_snapshot_sha256=report['factor_sha256'])[1] for mode in MODES}
    for record in report['folds']:
        folder=output/record['mode']/record['month']
        training,_=split_month(source_panel,record['month'])
        expected_config=experiment_config(folder,contracts[record['mode']])
        validate_fold_identity(record,record['mode'],record['month'],folder,expected_config,training.trade_date.max())
        load_model_preprocessing(record['model_path'],record['config'])
        if pd.Timestamp(record['train_end'])>=pd.Period(record['month'],freq='M').start_time:
            raise ValueError('Training leaked into evaluation month')
        if not 1<=record['epochs_completed']<=30 or not 1<=record['best_epoch']<=record['epochs_completed']:
            raise ValueError('Invalid best epoch')
        rows=json.loads((output/record['mode']/record['month']/'daily_metrics.json').read_text(encoding='utf-8'))
        validate_fold_metrics(record,rows,source_panel)
    if len(report['folds'])!=8:
        raise ValueError('Incomplete controlled experiment')
    for mode in MODES:
        rows=[]
        for month in MONTHS[:3]:
            rows.extend(json.loads((output/mode/month/'daily_metrics.json').read_text(encoding='utf-8')))
        if report['rolling_aggregate'][mode]!=summarize(rows):
            raise ValueError('Fold rolling aggregate mismatch')
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
