"""Label-free frozen Transformer scores. No fitting, registry or artifact writes."""
import numpy as np
import pandas as pd

from analysis.transformer_experiment import adjusted_head_scores, sha256
from analysis.transformer_features import build_feature_panel, load_model_preprocessing, prepare_inference_data


def history_windows(context, columns, mapping, requested_dates, length):
    """Align every stock to the same exact historical sessions, including unlabeled ends."""
    if (type(length) is not int or length <= 0 or not columns or not mapping
            or sorted(mapping.values()) != list(range(len(mapping)))):
        raise ValueError('Invalid window length/feature/mapping contract')
    frame = context.copy()
    frame['日期'] = pd.to_datetime(frame['日期'], errors='raise')
    if (frame[['日期', '股票代码', 'instrument']].isna().any().any()
            or frame.duplicated(['instrument', '日期']).any()
            or set(frame['股票代码']) != set(mapping)
            or not (frame['股票代码'].map(mapping) == frame.instrument).all()):
        raise ValueError('Invalid historical session identity/mapping')
    dates = sorted(frame['日期'].unique())
    indices = {pd.Timestamp(date): i for i, date in enumerate(dates)}
    groups = {index: group.sort_values('日期').set_index('日期') for index, group in frame.groupby('instrument')}
    requested = [pd.Timestamp(date) for date in requested_dates]
    if not requested or requested != sorted(set(requested)):
        raise ValueError('Invalid requested window dates')
    for date in requested:
        end = indices.get(date, -1)
        if end < length-1:
            raise ValueError('Insufficient historical window sessions')
        window_dates = dates[end-length+1:end+1]
        sequences = []
        for index in range(len(mapping)):
            stock = groups.get(index)
            if stock is None or not set(window_dates).issubset(stock.index):
                raise ValueError('Incomplete historical stock window sessions')
            values = stock.loc[window_dates, columns].to_numpy(dtype=np.float32)
            if not np.isfinite(values).all():
                raise ValueError('Nonfinite historical window features')
            sequences.append(values)
        yield date, np.stack(sequences), list(range(len(mapping)))


def infer_month(panel, record, month):
    """Use saved raw-history origins and scaler; targets never choose inference dates."""
    import torch
    from analysis.transformer_model import MultiHeadStockTransformer
    config = record['config']
    scaler, metadata = load_model_preprocessing(record['model_path'], config)
    first = pd.Period(month, freq='M').start_time
    following = first+pd.offsets.MonthBegin(1)
    if pd.Timestamp(record['train_end']) >= first:
        raise ValueError('Model training cutoff is not before signal month')
    # Truncate even the raw input to this month's last signal session. Features
    # are causal, but this also keeps following-month observations unavailable.
    observed = panel[pd.to_datetime(panel.trade_date) < following].copy()
    inference_config = dict(config, feature_start_date=metadata['feature_history_start'],
                            stock_history_starts=metadata['stock_history_starts'])
    raw, columns, _, _ = build_feature_panel(observed, inference_config, metadata['stockid2idx'],
                                            include_labels=False, use_parallel=True, n_workers=4)
    context = prepare_inference_data(raw, columns, scaler)
    dates = sorted(observed.loc[pd.to_datetime(observed.trade_date) >= first, 'trade_date'].unique())
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = MultiHeadStockTransformer(len(metadata['selected_features']), config,
                                      len(metadata['stockid2idx'])).to(device)
    model.load_state_dict(torch.load(record['model_path'], map_location=device, weights_only=True))
    model.eval()
    inverse = {index: code for code, index in metadata['stockid2idx'].items()}
    digest = sha256(record['model_path'])
    rows = []
    with torch.no_grad():
        for date, sequence, ids in history_windows(context, metadata['selected_features'],
                                                  metadata['stockid2idx'], dates, config['sequence_length']):
            outputs = model(torch.from_numpy(sequence).unsqueeze(0).to(device), return_all_heads=True)
            ranking, secondary = adjusted_head_scores(outputs, 'nonnegative_variance')
            if len(ranking) != len(ids) or not np.isfinite(ranking).all() or not np.isfinite(secondary).all():
                raise ValueError('Invalid model score coverage')
            rows.extend(dict(date=str(date.date()), ts_code=inverse[index], raw=float(ranking[j]),
                             nonnegative_variance=float(secondary[j]), model_sha256=digest)
                        for j, index in enumerate(ids))
    return sorted(rows, key=lambda row: (row['date'], row['ts_code']))
