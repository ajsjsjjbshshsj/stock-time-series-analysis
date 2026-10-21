"""Bounded small-pool adapter for the original Transformer, never HTTP imported."""
import numpy as np
import pandas as pd
from tasks.artifacts import read_json,write_json


def train_transformer(task,directory,source,emit):
    import torch
    from analysis.transformer_trainer import run_transformer_training
    from analysis.transformer_features import load_model_preprocessing
    request=task['request'];frame,calendar,contract=source.load(task)
    history=contract['source_history'].copy();history['trade_date']=pd.to_datetime(history.trade_date)
    if sorted(history.ts_code.unique())!=request['stock_codes']:raise ValueError('Transformer source pool changed')
    history=history[history.trade_date<=pd.Timestamp(request['end_date'])].copy()
    config=dict(output_dir=str(directory),feature_start_date=str(history.trade_date.min().date()),
        training_start_date=request['start_date'],num_epochs=request['num_epochs'],batch_size=request['batch_size'],
        use_probe_selection=True,probe_n_iter=1,probe_n_noise=10,feature_num='158+39',sequence_length=60,
        seed=42,score_adjustment_policy='nonnegative_variance')
    torch.set_num_threads(2)
    if torch.cuda.is_available():
        total=torch.cuda.get_device_properties(0).total_memory
        torch.cuda.set_per_process_memory_fraction(min(.8,4*1024**3/total));torch.cuda.reset_peak_memory_stats()
    emit('stage',stage='PROBE');emit('stage',stage='TRAIN')
    result=run_transformer_training(panel_df=history,config=config,create_plots=False,
        progress_callback=lambda completed,total:emit('progress',phase='TRAIN',completed=completed,total=total))
    config=result['config'];_,meta=load_model_preprocessing(result['model_path'],config)
    from analysis.transformer_model import MultiHeadStockTransformer
    model=MultiHeadStockTransformer(input_dim=len(result['feature_names']),config=config,num_stocks=len(result['stockid2idx']))
    model.load_state_dict(torch.load(result['model_path'],map_location='cpu',weights_only=True));model.eval()
    values=np.zeros((1,len(result['stockid2idx']),config['sequence_length'],len(result['feature_names'])),dtype=np.float32)
    with torch.no_grad():expected=model(torch.from_numpy(values),return_all_heads=True)['ranking'].reshape(-1).numpy()
    np.savez(directory/'reload.npz',values=values,predictions=expected)
    val_start=(history.trade_date.max()-pd.DateOffset(months=2)).normalize()
    return dict(feature_names=result['feature_names'],feature_count=len(result['feature_names']),score_units='ranking_score',
        horizon=5,holding_sessions=4,label_definition='open_T5/open_T1-1',train_start=request['start_date'],
        train_end=str((val_start-pd.Timedelta(days=1)).date()),seen_through=request['end_date'],
        pool_count=len(result['stockid2idx']),metrics=dict(best_validation_score=float(result['best_score'])),
        probe_selected=True,epochs_completed=result['epochs_completed'],config=config,stockid2idx=result['stockid2idx'],
        device='cuda' if torch.cuda.is_available() else 'cpu',peak_vram_bytes=int(torch.cuda.max_memory_allocated()) if torch.cuda.is_available() else 0)


def verify_transformer(directory,native):
    import torch
    from analysis.transformer_features import load_model_preprocessing
    from analysis.transformer_model import MultiHeadStockTransformer
    config=native['config'];_,meta=load_model_preprocessing(directory/'best_model.pth',config)
    if meta['selected_features']!=native['feature_names'] or meta['stockid2idx']!=native['stockid2idx']:
        raise ValueError('Transformer feature/pool identity changed')
    model=MultiHeadStockTransformer(input_dim=len(native['feature_names']),config=config,num_stocks=len(native['stockid2idx']))
    model.load_state_dict(torch.load(directory/'best_model.pth',map_location='cpu',weights_only=True));model.eval()
    with np.load(directory/'reload.npz',allow_pickle=False) as proof:
        values=proof['values'];expected=proof['predictions']
    if values.shape!=(1,len(native['stockid2idx']),config['sequence_length'],len(native['feature_names'])) or not np.isfinite(values).all():
        raise ValueError('Invalid Transformer reload proof')
    with torch.no_grad():actual=model(torch.from_numpy(values),return_all_heads=True)['ranking'].reshape(-1).numpy()
    if actual.shape!=expected.shape or not np.isfinite(actual).all() or not np.allclose(actual,expected,rtol=1e-5,atol=1e-6):
        raise ValueError('Transformer reload failed')
    return model
