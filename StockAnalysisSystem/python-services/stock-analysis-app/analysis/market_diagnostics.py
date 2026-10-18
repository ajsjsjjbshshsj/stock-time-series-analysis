"""Posthoc label diagnostics, NEVER inputs to eligibility or trading decisions."""
import numpy as np
import pandas as pd
from analysis.market_account import _frame
from analysis.strategy_dates import validate_session_calendar


def score_diagnostics(scores,prices,calendar):
    scored=_frame(scores,('score',));quoted=_frame(prices,('close',))
    if not np.isfinite(scored.score).all() or not np.isfinite(quoted.close).all() or (quoted.close<=0).any():
        raise ValueError('Invalid diagnostic scores/prices')
    opened=validate_session_calendar(calendar);positions={day:i for i,day in enumerate(opened)}
    if not set(scored.trade_date).issubset(positions):raise ValueError('Score outside diagnostic calendar')
    frame=scored.copy()
    frame['target_date']=[opened[positions[d]+5] if positions[d]+5<len(opened) else None for d in frame.trade_date]
    frame=frame.merge(quoted[['ts_code','trade_date','close']],on=['ts_code','trade_date'],how='left',validate='one_to_one')
    future=quoted[['ts_code','trade_date','close']].rename(columns=dict(trade_date='target_date',close='target_close'))
    frame=frame.merge(future,on=['ts_code','target_date'],how='left',validate='many_to_one')
    frame['label']=frame.target_close/frame.close-1
    matched=np.isfinite(frame.label.to_numpy(dtype=float))
    daily=[];ics=[]
    for day,all_rows in frame.groupby('trade_date',sort=True):
        group=all_rows[np.isfinite(all_rows.label)].copy();ic=None;top=None;bottom=None;mean=None
        if len(group):
            if group.score.nunique()>1 and group.label.nunique()>1:
                value=group[['score','label']].rank(method='average').corr().iloc[0,1]
                if np.isfinite(value):ic=float(value);ics.append(ic)
            count=max(1,len(group)//10);ordered=group.sort_values(['score','ts_code'],ascending=[True,False])
            top=float(ordered.tail(count).label.mean());bottom=float(ordered.head(count).label.mean());mean=float(group.label.mean())
        daily.append(dict(date=day,scored_rows=len(all_rows),matched_label_rows=len(group),rank_ic=ic,
            top_decile_mean_forward5d=top,bottom_decile_mean_forward5d=bottom,matched_universe_mean_forward5d=mean))
    valid=frame[matched];y=valid.label.to_numpy(dtype=float);prediction=valid.score.to_numpy(dtype=float)
    return dict(scored_rows=len(frame),matched_label_rows=int(matched.sum()),unmatched_label_rows=int((~matched).sum()),
        label_coverage=float(matched.mean()) if len(frame) else 0.,defined_daily_rank_ic=len(ics),
        mean_daily_rank_ic=float(np.mean(ics)) if ics else None,
        positive_daily_rank_ic_fraction=float((np.asarray(ics)>0).mean()) if ics else None,
        rmse=float(np.sqrt(np.mean((prediction-y)**2))) if len(valid) else None,
        zero_prediction_rmse=float(np.sqrt(np.mean(y*y))) if len(valid) else None,
        direction_accuracy=float((np.sign(prediction)==np.sign(y)).mean()) if len(valid) else None,
        label_definition='T raw close to fifth SSE-session raw close',daily=daily,
        warning='Posthoc matched-label subset only; overlapping labels, not executable portfolio P&L; never filters scores')
