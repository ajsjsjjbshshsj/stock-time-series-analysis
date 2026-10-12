"""Pure weighted ranking account. Scores choose weights, prices determine P&L."""
import numpy as np
import pandas as pd
from analysis.strategy_dates import validate_session_calendar, market_dates
from analysis.backtester import Backtester


def validate_ranking_history(prices, calendar):
    opened = validate_session_calendar(calendar)
    frame = market_dates(prices, ('open', 'close'))
    if frame.empty: raise ValueError('No market history')
    for col in ('open', 'close'):
        frame[col] = pd.to_numeric(frame[col], errors='raise')
        if not np.isfinite(frame[col]).all() or (frame[col] <= 0).any():
            raise ValueError('Invalid market prices')
    dates = sorted(frame.trade_date.unique())
    if not calendar['start'] <= dates[0] <= dates[-1] <= calendar['end']:
        raise ValueError('Calendar lacks market coverage')
    if dates != [d for d in opened if dates[0] <= d <= dates[-1]]:
        raise ValueError('Market session history incomplete')
    codes = sorted(frame.ts_code.unique())
    if any(set(group.ts_code) != set(codes) for _, group in frame.groupby('trade_date')):
        raise ValueError('Market stock pool incomplete')
    return frame, dates, codes


def _simulate(frame, dates, cycles, selections, initial, fee, slip):
    lookup = frame.set_index(['trade_date', 'ts_code'])
    entries = {cycle['entry_date']: cycle for cycle in cycles}
    cash, positions = initial, {}
    trades, equity = [], []
    for day in dates:
        if day in entries:
            cycle = entries[day]
            for code, units in positions.items():
                quoted = float(lookup.loc[(day, code), 'open'])
                executed = quoted*(1-slip)
                notional = units*executed
                charge = notional*fee
                cash += notional-charge
                trades.append(dict(date=pd.Timestamp(day), signal_date=cycle['signal_date'],
                                   ts_code=code, action='SELL', units=units, price=executed,
                                   notional=notional, fee=charge, slippage_cost=units*(quoted-executed)))
            positions = {}
            budget = cash
            for code, weight in selections[cycle['signal_date']].items():
                quoted = float(lookup.loc[(day, code), 'open'])
                executed = quoted*(1+slip)
                notional = budget*weight/(1+fee)
                units, charge = notional/executed, notional*fee
                cash -= notional+charge
                positions[code] = units
                trades.append(dict(date=pd.Timestamp(day), signal_date=cycle['signal_date'],
                                   ts_code=code, action='BUY', units=units, price=executed,
                                   notional=notional, fee=charge, slippage_cost=units*(executed-quoted)))
            if cash < -max(1e-8, budget*1e-12):
                raise ValueError('Account exceeded cash budget')
            cash = max(0., cash)
        equity.append(dict(date=pd.Timestamp(day), cash=cash,
                           equity=cash+sum(units*float(lookup.loc[(day, c), 'close']) for c, units in positions.items())))
    curve = pd.DataFrame(equity)
    values = curve.equity.to_numpy()
    returns = pd.Series(values/np.r_[initial, values[:-1]]-1, index=curve.date)
    curve['daily_return'] = returns.to_numpy()
    curve['cumulative_return'] = values/initial-1
    metrics = Backtester(initial)._calculate_metrics(returns)
    metrics.update(final_equity=float(values[-1]), total_return=float(values[-1]/initial-1),
                   total_fees=float(sum(t['fee'] for t in trades)),
                   total_slippage=float(sum(t['slippage_cost'] for t in trades)),
                   total_traded_notional=float(sum(t['notional'] for t in trades)), n_trades=len(trades))
    return dict(equity_curve=curve, trades=pd.DataFrame(trades, columns=['date', 'signal_date', 'ts_code',
                       'action', 'units', 'price', 'notional', 'fee', 'slippage_cost']), daily_returns=returns, metrics=metrics)


def run_weighted_ranking_account(prices, scores, *, calendar, top_n=10, rebalance_days=5,
                                 initial_capital=1000000., commission_rate=.0003, slippage=.001):
    frame, dates, codes = validate_ranking_history(prices, calendar)
    if (type(top_n) is not int or not 1 <= top_n <= len(codes)
            or type(rebalance_days) is not int or rebalance_days < 1
            or any(isinstance(v, bool) or not np.isfinite(v) for v in (initial_capital, commission_rate, slippage))
            or initial_capital <= 0 or not 0 <= commission_rate < 1 or not 0 <= slippage < 1):
        raise ValueError('Invalid ranking account configuration')
    scored = market_dates(scores, ('pred_return',))
    scored['pred_return'] = pd.to_numeric(scored.pred_return, errors='raise')
    if not np.isfinite(scored.pred_return).all() or not set(scored.ts_code).issubset(codes):
        raise ValueError('Invalid ranking scores')
    cycles, selections = [], {}
    for i in range(0, len(dates)-1, rebalance_days):
        day = dates[i]
        group = scored[scored.trade_date == day]
        if set(group.ts_code) != set(codes) or len(group) != len(codes):
            raise ValueError('Incomplete ranking signal pool/date')
        selected = group.sort_values(['pred_return', 'ts_code'], ascending=[False, True]).head(top_n)
        selected = selected[selected.pred_return > 0]
        weights = dict(zip(selected.ts_code, selected.pred_return/selected.pred_return.sum())) if not selected.empty else {}
        selections[day] = weights
        cycles.append(dict(signal_date=day, entry_date=dates[i+1], stocks=weights))
    if not cycles: raise ValueError('No executable ranking signal')
    result = _simulate(frame, dates, cycles, selections, float(initial_capital), commission_rate, slippage)
    benchmark = _simulate(frame, dates, cycles,
                          {c['signal_date']: {code: 1/len(codes) for code in codes} for c in cycles},
                          float(initial_capital), commission_rate, slippage)
    result['metrics']['benchmark'] = benchmark['metrics']
    result['metrics']['excess_return'] = result['metrics']['total_return']-benchmark['metrics']['total_return']
    result['benchmark_curve'] = benchmark['equity_curve']
    result['trade_log'] = cycles
    result['execution_assumptions'] = dict(signal='T close', fill='T+1 open', mark='close',
        rebalance_days=rebalance_days, commission_rate=commission_rate, slippage=slippage,
        terminal='mark remaining positions, no future liquidation',
        price_basis='input prices; raw prices exclude corporate-action adjustment',
        units='fractional research units; no lots, limit/suspension fills or capacity simulation')
    return result
