"""Actual-price, explicit-horizon research reports; negative returns are valid."""
import pandas as pd
from tasks.artifacts import write_json
from analysis.market_account import run_market_account
from .inference import collect_predictions


def backtest_operation(task,directory,source,emit):
    emit('stage',stage='INPUT');coverage,score_parts,price_parts=collect_predictions(task,directory,source,emit,keep_prices=True)
    scores=pd.concat(score_parts,ignore_index=True);prices=pd.concat(price_parts,ignore_index=True)
    score_parts.clear();price_parts.clear()
    prices=prices[['ts_code','trade_date','open','high','low','close','vol']].copy()
    row,metadata=source.model(task['request']['model_id']);native=metadata['native'];r=task['request']
    factors,limits,calendar,halts,proof=source.execution_inputs(task,prices)
    factors.to_parquet(directory/'factors.parquet',index=False);limits.to_parquet(directory/'limits.parquet',index=False)
    data=task['binding'].get('data',{});life=data.get('lifecycle',{}).get('stocks',{})
    delist={code:v['delist_date'] for code,v in life.items() if v.get('delist_date')}
    emit('stage',stage='TRAIN')  # Existing queue calls its bounded compute phase TRAIN.
    result=run_market_account(prices,scores[['ts_code','trade_date','score']],factors,limits,calendar,
        top_n=1 if r['algorithm']=='xgboost' else r['top_n'],initial_capital=r['initial_capital'],
        fee_bps=r['fee_bps'],slippage_bps=r['slippage_bps'],suspended=halts,
        signal_start=coverage['signal_start'],signal_end=coverage['signal_end'],holding_sessions=native['holding_sessions'],
        entry_score_min=.5 if r['algorithm']=='xgboost' else None,price_precision='legacy_float32_decimal6',delist_dates=delist)
    report=dict(coverage,status='COMPLETE' if result['status']=='READY' else 'NOT_READY',
        score_units=native['score_units'],horizon=native['horizon'],holding_sessions=native['holding_sessions'],
        model_manifest_sha256=row['manifest_sha256'],seen_through=native['seen_through'],
        sample_relation='historical_research' if coverage['signal_start']<=native['seen_through'] else 'post_model_selection',
        execution_evidence=proof,published=False,warning='Retrospective fractional-unit research, not live execution or guaranteed returns')
    report.update(requested_start=r['start_date'],requested_end=r['end_date'],execution_end=str(pd.to_datetime(prices.trade_date).max().date()))
    if result['status']=='READY':
        report.update(metrics=result['metrics'],assumptions=result['assumptions'])
        for section in ('equity','trades','orders','writeoffs'):
            pd.DataFrame(result[section]).to_parquet(directory/(section+'.parquet'),index=False)
        write_json(directory/'open_positions.json',result['open_positions'])
    else:
        missing=result.get('missing',[]);report.update(missing_count=len(missing),missing=missing[:100])
        pd.DataFrame(missing).to_parquet(directory/'requirements.parquet',index=False)
    write_json(directory/'report.json',report);return report
