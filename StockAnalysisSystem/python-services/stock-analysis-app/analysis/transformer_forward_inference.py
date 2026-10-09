"""Unclassified frozen scores; publication eligibility belongs to the store."""
import pandas as pd
from analysis.transformer_forward_contracts import frame_hash,market_frame
from analysis.transformer_forward_diagnostic import latest_scores, validate_diagnostic


def validate_scores(rows,codes,date,model_sha):
    result=validate_diagnostic(rows,codes,date,model_sha)
    result.pop('kind')
    return result


def score_session(panel,record,date,history_proof):
    """Only a fully bound panel may reach the frozen model and scaler."""
    from analysis.transformer_features import load_model_preprocessing
    _,metadata=load_model_preprocessing(record['model_path'],record['config'])
    normalized=market_frame(panel)
    codes=sorted(metadata['stockid2idx'])
    starts={c:str(pd.Timestamp(d).date()) for c,d in metadata['stock_history_starts'].items()}
    if (history_proof.get('signal_date')!=date or history_proof.get('codes')!=codes
            or history_proof.get('history_starts')!=starts or set(normalized.ts_code)!=set(codes)
            or history_proof.get('model_panel_sha256')!=frame_hash(panel)
            or not history_proof.get('dates') or history_proof['dates'][-1]!=date):
        raise ValueError('Scoring requires matching full historical proof')
    for c in codes:
        expected=[d for d in history_proof['dates'] if d>=starts[c]]
        if normalized.loc[normalized.ts_code==c,'trade_date'].tolist()!=expected:
            raise ValueError('Frozen scoring panel is missing historical sessions')
    rows=latest_scores(panel,record,date)
    validate_scores(rows,codes,date,rows[0]['model_sha256'])
    return rows


