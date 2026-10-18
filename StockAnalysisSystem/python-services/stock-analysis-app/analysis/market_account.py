"""Sparse-market fractional adjusted-unit research ledger, NOT live execution.

Scores choose orders at T close; only actual prices determine fills/valuation.
Daily limit checks are conservative proxies, not order-queue/capacity models.
"""
import math
import numpy as np
import pandas as pd
from data_processor.feature_store_contract import validate_stock_code
from analysis.strategy_dates import market_dates,validate_session_calendar


def _frame(frame,columns):
    result=market_dates(frame,columns)
    for code in result.ts_code:validate_stock_code(code)
    for name in columns:
        if result[name].map(lambda v:isinstance(v,(bool,np.bool_))).any():raise ValueError('Boolean execution value')
        result[name]=pd.to_numeric(result[name],errors='raise')
        if np.isinf(result[name].to_numpy(dtype=float)).any():raise ValueError('Infinite execution value')
    return result


def execution_readiness(prices,factors,limits):
    """Explicit per-observation requirements, never a full date/code cross join."""
    p=_frame(prices,('open','high','low','close','vol'))
    f=_frame(factors,('adj_factor',));l=_frame(limits,('up_limit','down_limit'))
    values=p[['open','high','low','close','vol']].to_numpy(dtype=float)
    if (not np.isfinite(values).all() or (p[['open','high','low','close']]<=0).any().any()
            or (p.vol<0).any() or (p.high<p[['open','low','close']].max(axis=1)).any()
            or (p.low>p[['open','high','close']].min(axis=1)).any()):raise ValueError('Invalid execution OHLCV')
    key=['ts_code','trade_date'];joined=p[key].merge(f,on=key,how='left',validate='one_to_one').merge(l,on=key,how='left',validate='one_to_one')
    if ((l.up_limit>0)&(l.down_limit>0)&(l.up_limit<l.down_limit)).any():raise ValueError('Invalid execution bound order')
    factor=~(joined.adj_factor.notna()&(joined.adj_factor>0))
    limit=~(joined.up_limit.notna()&joined.down_limit.notna()&(joined.up_limit>0)&(joined.down_limit>0))
    missing=[]
    for kind,mask in (('adj_factor',factor),('stk_limit',limit)):
        missing.extend(dict(kind=kind,ts_code=r.ts_code,trade_date=r.trade_date) for r in joined[mask].itertuples())
    return dict(status='NOT_READY' if missing else 'READY',missing=missing),p,f,l


def run_market_account(prices,scores,factors,limits,calendar,*,top_n=10,
                       initial_capital=1000000.,fee_bps=3.,slippage_bps=10.,suspended=(),
                       signal_start=None,signal_end=None):
    """Five-session cohorts: T+1 open buy, T+6 open planned sale.

    top_n=None is the same-rule eligible-universe equal-weight benchmark.
    Unfilled buy slots remain cash. Expired unfilled sell lots keep encumbered
    capital and retry on later sessions. No outcome-conditioned replacements.
    """
    if top_n is not None and (type(top_n) is not int or not 1<=top_n<=10000):raise ValueError('Invalid top_n')
    for value in (initial_capital,fee_bps,slippage_bps):
        if isinstance(value,bool) or not isinstance(value,(float,int)) or not math.isfinite(value):raise ValueError('Invalid account number')
    if initial_capital<=0 or not 0<=fee_bps<10000 or not 0<=slippage_bps<10000:raise ValueError('Invalid account budget/cost')
    ready,p,f,l=execution_readiness(prices,factors,limits)
    if ready['status']!='READY':return ready
    scored=_frame(scores,('score',));opened=validate_session_calendar(calendar)
    if p.empty or scored.empty:raise ValueError('Empty account source/signals')
    if not np.isfinite(scored.score).all():raise ValueError('Invalid score')
    position={d:i for i,d in enumerate(opened)}
    if not set(p.trade_date).issubset(position) or not set(scored.trade_date).issubset(position):raise ValueError('Observation outside calendar')
    quote_keys=set(zip(p.ts_code,p.trade_date))
    if not set(zip(scored.ts_code,scored.trade_date)).issubset(quote_keys):raise ValueError('Signal has no observed quote')
    halt=set()
    for code,day in suspended:
        validate_stock_code(code)
        if day not in position:raise ValueError('Suspension outside calendar')
        halt.add((code,day))
    first=signal_start or scored.trade_date.min();last=signal_end or scored.trade_date.max()
    if first not in position or last not in position or first>last:raise ValueError('Invalid signal interval')
    end=p.trade_date.max();cycles={}
    for i in range(position[first],position[last]+1,5):
        if i+6>=len(opened) or opened[i+6]>end:
            return dict(status='NOT_READY',missing=[dict(kind='execution_horizon',trade_date=opened[i])])
        group=scored[scored.trade_date==opened[i]].sort_values(['score','ts_code'],ascending=[False,True])
        count=len(group) if top_n is None else top_n
        chosen=group if top_n is None else group.head(top_n)
        cycles[opened[i+1]]=dict(signal_date=opened[i],exit_date=opened[i+6],codes=chosen.ts_code.tolist(),slots=count)
    lookup={ (r.ts_code,r.trade_date):r for r in p.itertuples() }
    factors_map={(r.ts_code,r.trade_date):float(r.adj_factor) for r in f.itertuples()}
    limits_map={(r.ts_code,r.trade_date):(float(r.down_limit),float(r.up_limit)) for r in l.itertuples()}
    cash=float(initial_capital);positions={};trades=[];orders=[];equity=[]
    fee=fee_bps/10000.;slip=slippage_bps/10000.

    def failure(kind,code,day):
        # A partial ledger is diagnostic only; never attach performance metrics.
        return dict(status='NOT_READY',missing=[dict(kind=kind,ts_code=code,trade_date=day)],orders=orders,trades=trades)

    def fill(code,day,side):
        row=lookup.get((code,day))
        if row is None:
            return (None,'SUSPENDED') if (code,day) in halt else (None,'UNEXPLAINED_EXECUTION_GAP')
        if row.vol<=0 or (code,day) in halt:return None,'NO_VOLUME_OR_SUSPENDED'
        lower,upper=limits_map[(code,day)]
        if not lower<=row.open<=upper:return None,'QUOTE_OUTSIDE_LIMITS'
        if side=='BUY' and row.open>=upper:return None,'UP_LIMIT'
        if side=='SELL' and row.open<=lower:return None,'DOWN_LIMIT'
        executed=float(row.open)*(1+slip if side=='BUY' else 1-slip)
        if not max(lower,row.low)<=executed<=min(upper,row.high):return None,'SLIPPAGE_OUTSIDE_RANGE'
        return (executed,executed*factors_map[(code,day)]),None

    for day in opened[position[first]:position[end]+1]:
        for code,lot in list(positions.items()):
            if day<lot['exit_date']:continue
            executed,reason=fill(code,day,'SELL')
            if reason:
                orders.append(dict(date=day,ts_code=code,action='SELL',reason=reason))
                if reason in ('UNEXPLAINED_EXECUTION_GAP','QUOTE_OUTSIDE_LIMITS'):return failure(reason,code,day)
                continue
            raw,adjusted=executed;notional=lot['units']*adjusted;charge=notional*fee
            cash+=notional-charge
            trades.append(dict(date=day,signal_date=lot['signal_date'],ts_code=code,action='SELL',
                raw_execution_price=raw,adjusted_execution_price=adjusted,units=lot['units'],notional=notional,fee=charge))
            del positions[code]
        if day in cycles:
            cycle=cycles[day];budget=cash/cycle['slots'] if cycle['slots'] else 0.
            for code in cycle['codes']:
                if code in positions:orders.append(dict(date=day,ts_code=code,action='BUY',reason='POSITION_STILL_OPEN'));continue
                executed,reason=fill(code,day,'BUY')
                if reason:
                    orders.append(dict(date=day,ts_code=code,action='BUY',reason=reason))
                    if reason in ('UNEXPLAINED_EXECUTION_GAP','QUOTE_OUTSIDE_LIMITS'):return failure(reason,code,day)
                    continue
                if budget<=0:continue
                raw,adjusted=executed;notional=budget/(1+fee);charge=notional*fee;units=notional/adjusted
                cash-=notional+charge
                if cash<-max(1e-8,initial_capital*1e-12):raise ValueError('Cash budget exceeded')
                cash=max(0.,cash)
                positions[code]=dict(units=units,exit_date=cycle['exit_date'],signal_date=cycle['signal_date'],last_mark=adjusted)
                trades.append(dict(date=day,signal_date=cycle['signal_date'],ts_code=code,action='BUY',
                    raw_execution_price=raw,adjusted_execution_price=adjusted,units=units,notional=notional,fee=charge))
        value=cash;stale=0
        for code,lot in positions.items():
            row=lookup.get((code,day))
            if row is not None:lot['last_mark']=float(row.close)*factors_map[(code,day)]
            elif (code,day) in halt:stale+=1
            else:return failure('UNEXPLAINED_VALUATION_GAP',code,day)
            value+=lot['units']*lot['last_mark']
        if not math.isfinite(value):raise ValueError('Nonfinite equity')
        equity.append(dict(date=day,cash=cash,equity=value,stale_positions=stale))
    values=np.array([initial_capital,*[r['equity'] for r in equity]])
    metrics=dict(total_return=float(values[-1]/initial_capital-1),final_equity=float(values[-1]),
        max_drawdown=float(np.min(values/np.maximum.accumulate(values)-1)),total_fees=float(sum(t['fee'] for t in trades)),
        n_trades=len(trades),unfilled_orders=len(orders),open_positions=len(positions),
        stale_valuation_days=sum(r['stale_positions']>0 for r in equity),
        traded_notional=float(sum(t['notional'] for t in trades)))
    return dict(status='READY',metrics=metrics,equity=equity,trades=trades,orders=orders,open_positions=positions,
        assumptions=dict(signal='T close',buy='T+1 open',planned_sell='T+6 open',rebalance_sessions=5,
            top_n=top_n,fee_bps=fee_bps,slippage_bps=slippage_bps,units='fractional adjusted-return research proxy',
            terminal='mark remaining positions without invented liquidation',
            costs='research stress assumptions, not historical statutory taxes/minimum commissions',
            limitations='no order queues, capacity, board lots or actual corporate-action cash accounting'))
