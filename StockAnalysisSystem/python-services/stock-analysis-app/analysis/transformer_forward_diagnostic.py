"""Historical last-session diagnostic, never a retrospectively claimed blind signal."""
from numbers import Real
import math

import numpy as np
import pandas as pd

from analysis.transformer_experiment import adjusted_head_scores, sha256
from analysis.transformer_features import build_feature_panel, load_model_preprocessing, prepare_inference_data
from analysis.transformer_portfolio_signals import history_windows


def validate_diagnostic(rows, codes, date, model_sha):
    required = {'date', 'ts_code', 'raw', 'nonnegative_variance', 'model_sha256'}
    if (len(codes) != 20 or len(set(codes)) != 20 or len(rows) != 20
            or [r.get('ts_code') for r in rows] != sorted(codes)
            or any(set(r) != required or r['date'] != date or r['model_sha256'] != model_sha for r in rows)
            or any(not isinstance(r[p], Real) or isinstance(r[p], bool) or not math.isfinite(r[p])
                   for r in rows for p in ('raw', 'nonnegative_variance'))):
        raise ValueError('Diagnostic coverage/date/hash/score mismatch')
    return dict(kind='historical_diagnostic', date=date, model_sha256=model_sha,
                top5={p: [r['ts_code'] for r in sorted(rows, key=lambda r: (-r[p], r['ts_code']))[:5]]
                      for p in ('raw', 'nonnegative_variance')})


def latest_scores(panel, record, date):
    """Single historical day from saved scaler; no target maturity filter or fitting."""
    import torch
    from analysis.transformer_model import MultiHeadStockTransformer
    observed = panel[pd.to_datetime(panel.trade_date) <= pd.Timestamp(date)].copy()
    if observed.empty or pd.to_datetime(observed.trade_date).max() != pd.Timestamp(date):
        raise ValueError('Diagnostic session is unavailable')
    config = record['config']
    scaler, metadata = load_model_preprocessing(record['model_path'], config)
    inference = dict(config, feature_start_date=metadata['feature_history_start'],
                     stock_history_starts=metadata['stock_history_starts'])
    raw, columns, _, _ = build_feature_panel(observed, inference, metadata['stockid2idx'],
                                            include_labels=False, use_parallel=True, n_workers=4)
    context = prepare_inference_data(raw, columns, scaler)
    _, values, ids = next(history_windows(context, metadata['selected_features'], metadata['stockid2idx'],
                                         [date], config['sequence_length']))
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = MultiHeadStockTransformer(len(metadata['selected_features']), config,
                                      len(metadata['stockid2idx'])).to(device)
    model.load_state_dict(torch.load(record['model_path'], map_location=device, weights_only=True))
    model.eval()
    with torch.no_grad():
        outputs = model(torch.from_numpy(values).unsqueeze(0).to(device), return_all_heads=True)
        ranking, secondary = adjusted_head_scores(outputs, 'nonnegative_variance')
    if len(ranking) != len(ids) or len(secondary) != len(ids):
        raise ValueError('Diagnostic model score shape mismatch')
    inverse = {i: code for code, i in metadata['stockid2idx'].items()}
    digest = sha256(record['model_path'])
    rows = sorted([dict(date=date, ts_code=inverse[i], raw=float(ranking[j]),
                        nonnegative_variance=float(secondary[j]), model_sha256=digest)
                   for j, i in enumerate(ids)], key=lambda r: r['ts_code'])
    validate_diagnostic(rows, sorted(metadata['stockid2idx']), date, digest)
    return rows
