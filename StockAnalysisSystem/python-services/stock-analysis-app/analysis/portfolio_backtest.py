"""Pure nonoverlapping price-driven cash ledger, not a live execution simulator.

Adjusted fractional asset units proxy returns. No model, provider, database or
filesystem dependency; scores select stocks but never substitute for prices.
"""
from numbers import Real
import numpy as np
import pandas as pd

POLICIES = ('raw', 'nonnegative_variance')
SCENARIOS = (
    dict(id='gross', buy_fee_bps=0., sell_fee_bps=0., slippage_bps=0.),
    dict(id='fee3', buy_fee_bps=3., sell_fee_bps=3., slippage_bps=0.),
    dict(id='fee3_slip5', buy_fee_bps=3., sell_fee_bps=3., slippage_bps=5.),
    dict(id='fee3_slip10', buy_fee_bps=3., sell_fee_bps=3., slippage_bps=10.),
)


def validate_prices(prices):
    required = {'ts_code', 'trade_date', 'open', 'close'}
    if prices.empty or not required.issubset(prices) or prices.columns.duplicated().any():
        raise ValueError('Incomplete market price schema')
    frame = prices[list(sorted(required))].copy()
    frame['trade_date'] = pd.to_datetime(frame.trade_date, errors='raise')
    if frame[['ts_code', 'trade_date']].isna().any().any() or frame.duplicated(['ts_code', 'trade_date']).any():
        raise ValueError('Duplicate or missing market price identity')
    if not frame.ts_code.map(lambda code: isinstance(code, str) and bool(code)).all():
        raise ValueError('Invalid market stock code')
    if (not np.isfinite(frame[['open', 'close']].to_numpy(dtype=float)).all()
            or (frame[['open', 'close']] <= 0).any().any()):
        raise ValueError('Invalid market prices')
    codes = set(frame.ts_code)
    if any(set(group.ts_code) != codes for _, group in frame.groupby('trade_date')):
        raise ValueError('Incomplete market price coverage')
    return frame.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)


def make_schedule(prices, start_date, end_signal_date):
    """A T signal's T+5 exit coincides with the next T+4 signal's T+1 entry."""
    frame = validate_prices(prices)
    dates = sorted(frame.trade_date.unique())
    start, end = pd.Timestamp(start_date), pd.Timestamp(end_signal_date)
    if pd.isna(start) or pd.isna(end) or start > end:
        raise ValueError('Invalid schedule dates')
    candidates = [i for i, date in enumerate(dates) if start <= date <= end]
    if not candidates:
        raise ValueError('No signal dates')
    schedule = []
    for index in range(candidates[0], candidates[-1]+1, 4):
        if index+5 >= len(dates):
            raise ValueError('Incomplete exit price horizon')
        schedule.append(dict(signal_date=str(pd.Timestamp(dates[index]).date()),
                             entry_date=str(pd.Timestamp(dates[index+1]).date()),
                             exit_date=str(pd.Timestamp(dates[index+5]).date())))
    return schedule


def validate_config(config):
    required = {'initial_capital', 'buy_fee_bps', 'sell_fee_bps', 'slippage_bps', 'policy'}
    if set(config) != required or config['policy'] not in POLICIES:
        raise ValueError('Invalid portfolio config keys/policy')
    for key in required-{'policy'}:
        value = config[key]
        if not isinstance(value, Real) or isinstance(value, bool) or not np.isfinite(value):
            raise ValueError('Portfolio config must be finite numeric values')
    if config['initial_capital'] <= 0 or any(not 0 <= config[k] < 10000 for k in required-{'policy', 'initial_capital'}):
        raise ValueError('Invalid portfolio config capital/costs')


def _selected_signals(signals, schedule, codes, policy, top_k):
    if (signals.empty or not {'date', 'ts_code', *POLICIES}.issubset(signals)
            or signals.columns.duplicated().any()):
        raise ValueError('Incomplete signal schema')
    frame = signals.copy()
    frame['date'] = pd.to_datetime(frame.date, errors='raise')
    if (frame[['date', 'ts_code']].isna().any().any() or frame.duplicated(['date', 'ts_code']).any()
            or not set(frame.ts_code).issubset(codes)
            or not np.isfinite(frame[list(POLICIES)].to_numpy(dtype=float)).all()):
        raise ValueError('Invalid/duplicate/nonfinite signal observations')
    selected = {}
    for cycle in schedule:
        day = frame[frame.date == pd.Timestamp(cycle['signal_date'])]
        if set(day.ts_code) != codes or len(day) != len(codes):
            raise ValueError('Incomplete signal stock/date coverage')
        selected[cycle['signal_date']] = list(day.sort_values([policy, 'ts_code'],
                                                            ascending=[False, True]).head(top_k).ts_code)
    return selected


def _performance(equity, initial, trades, turnover, cycles):
    values = np.array([row['equity'] for row in equity], dtype=float)
    peaks = np.maximum.accumulate(values)
    drawdowns = values/peaks-1
    trough = int(np.argmin(drawdowns))
    peak = int(np.argmax(values[:trough+1]))
    monthly, previous = {}, initial
    for month in dict.fromkeys(row['date'][:7] for row in equity):
        rows = [row for row in equity if row['date'].startswith(month)]
        end_value = rows[-1]['equity']
        monthly[month] = dict(return_=end_value/previous-1, start_equity=previous,
                             end_equity=end_value, first_date=rows[0]['date'], last_date=rows[-1]['date'])
        monthly[month]['return'] = monthly[month].pop('return_')
        previous = end_value
    return dict(total_return=values[-1]/initial-1, final_equity=float(values[-1]),
                max_drawdown=float(drawdowns[trough]), drawdown_peak_date=equity[peak]['date'],
                drawdown_trough_date=equity[trough]['date'], first_date=equity[0]['date'],
                last_date=equity[-1]['date'], days=len(equity), cycles=len(cycles),
                total_fees=sum(t['fee'] for t in trades), total_slippage=sum(t['slippage_cost'] for t in trades),
                total_traded_notional=sum(t['notional'] for t in trades),
                summed_two_sided_turnover=sum(row['two_sided_turnover'] for row in turnover),
                summed_one_sided_turnover=sum(row['one_sided_turnover'] for row in turnover)), monthly


def run_portfolio(prices, signals, config, start_date, end_signal_date, top_k=5):
    """Close-mark a single cash account; all positions expire before the next buy.

    Buy costs are included in the equal budget, not charged on top of leverage.
    Every cycle fully liquidates (including retained names), then buys again.
    """
    validate_config(config)
    frame = validate_prices(prices)
    codes = set(frame.ts_code)
    if type(top_k) is not int or not 1 <= top_k <= len(codes):
        raise ValueError('Invalid portfolio top_k config')
    schedule = make_schedule(frame, start_date, end_signal_date)
    selection = _selected_signals(signals, schedule, codes, config['policy'], top_k)
    lookup = frame.set_index(['trade_date', 'ts_code'])
    entries = {c['entry_date']: c for c in schedule}
    dates = sorted(frame.loc[(frame.trade_date >= pd.Timestamp(schedule[0]['signal_date']))
                            & (frame.trade_date <= pd.Timestamp(schedule[-1]['exit_date'])), 'trade_date'].unique())
    initial = float(config['initial_capital'])
    buy_fee, sell_fee, slip = (float(config[key])/10000 for key in ('buy_fee_bps', 'sell_fee_bps', 'slippage_bps'))
    cash, positions, active = initial, {}, None
    trades, equity, cycles, turnover = [], [], [], []
    peak = initial
    for date in dates:
        text_date = str(pd.Timestamp(date).date())
        opening_equity = cash+sum(units*float(lookup.loc[(date, code), 'open']) for code, units in positions.items())
        bought, sold = 0., 0.
        if active is not None and text_date == active['exit_date']:
            for code, units in positions.items():
                quoted = float(lookup.loc[(date, code), 'open'])
                executed = quoted*(1-slip)
                notional = units*executed
                fee = notional*sell_fee
                cash += notional-fee
                sold += notional
                trades.append(dict(date=text_date, signal_date=active['signal_date'], ts_code=code,
                                   action='SELL', units=units, quoted_price=quoted, execution_price=executed,
                                   notional=notional, fee=fee, slippage_cost=units*(quoted-executed),
                                   cash_delta=notional-fee))
            cycles.append(dict(active, end_cash=cash, return_=cash/active['start_cash']-1))
            cycles[-1]['return'] = cycles[-1].pop('return_')
            positions, active = {}, None
        if text_date in entries:
            if positions or active is not None:
                raise ValueError('Overlapping capital allocation')
            cycle = entries[text_date]
            active = dict(cycle, stocks=selection[cycle['signal_date']], start_cash=cash)
            allocation = cash/top_k
            for code in active['stocks']:
                quoted = float(lookup.loc[(date, code), 'open'])
                executed = quoted*(1+slip)
                units = allocation/(executed*(1+buy_fee))
                notional = units*executed
                fee = notional*buy_fee
                positions[code] = units
                bought += notional
                trades.append(dict(date=text_date, signal_date=cycle['signal_date'], ts_code=code,
                                   action='BUY', units=units, quoted_price=quoted, execution_price=executed,
                                   notional=notional, fee=fee, slippage_cost=units*(executed-quoted),
                                   cash_delta=-allocation))
            cash = 0.  # Equal budget exactly exhausts available cash INCLUDING fees.
        position_value = sum(units*float(lookup.loc[(date, code), 'close']) for code, units in positions.items())
        value = cash+position_value
        if not np.isfinite(value) or value <= 0 or cash < 0:
            raise ValueError('Invalid/nonpositive equity or negative cash')
        previous = equity[-1]['equity'] if equity else initial
        peak = max(peak, value)
        equity.append(dict(date=text_date, cash=cash, position_value=position_value, equity=value,
                           daily_return=value/previous-1, cumulative_return=value/initial-1, drawdown=value/peak-1))
        if bought or sold:
            turnover.append(dict(date=text_date, buy_notional=bought, sell_notional=sold,
                                 pretrade_open_equity=opening_equity,
                                 two_sided_turnover=(bought+sold)/opening_equity,
                                 one_sided_turnover=(bought+sold)/(2*opening_equity)))
    if positions or active is not None:
        raise ValueError('Unclosed final position')
    metrics, monthly = _performance(equity, initial, trades, turnover, cycles)
    return dict(config=dict(config), top_k=top_k, schedule=schedule, trades=trades, equity=equity,
                cycles=cycles, turnover=turnover, metrics=metrics, monthly=monthly)
