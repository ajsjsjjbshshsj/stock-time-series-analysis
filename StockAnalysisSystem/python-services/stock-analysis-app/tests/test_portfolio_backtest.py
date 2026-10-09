"""Literal cash/price accounting examples, independent of model scores."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('analysis.portfolio_backtest')


def fixture(days=10):
    dates = pd.bdate_range('2026-06-25', periods=days)
    prices = pd.DataFrame([dict(ts_code=code, trade_date=date, open=10., close=10.)
                           for date in dates for code in ('A', 'B', 'C', 'D', 'E', 'F')])
    signals = pd.DataFrame([dict(date=date, ts_code=code, raw=6-i, nonnegative_variance=6-i)
                            for date in dates for i, code in enumerate(('A', 'B', 'C', 'D', 'E', 'F'))])
    config = dict(initial_capital=1000., buy_fee_bps=0., sell_fee_bps=0., slippage_bps=0., policy='raw')
    return dates, prices, signals, config


def run(prices, signals, config, dates, end=0):
    return api().run_portfolio(prices, signals, config, str(dates[0].date()), str(dates[end].date()))


def test_actual_prices_determine_realized_return_not_prediction_magnitude():
    dates, prices, signals, config = fixture()
    prices.loc[prices.trade_date == dates[5], ['open', 'close']] = 12.
    a = run(prices, signals, config, dates)
    signals['raw'] *= 10000
    b = run(prices, signals, config, dates)
    assert a['metrics']['final_equity'] == pytest.approx(1200.)
    assert a['metrics']['total_return'] == pytest.approx(.2)
    assert b['equity'] == a['equity']
    assert a['cycles'][0]['entry_date'] == str(dates[1].date())
    assert a['cycles'][0]['exit_date'] == str(dates[5].date())


def test_four_interval_schedule_does_not_reset_at_month_boundary():
    dates, prices, _, _ = fixture(15)
    schedule = api().make_schedule(prices, str(dates[0].date()), str(dates[8].date()))
    assert [s['signal_date'] for s in schedule] == [str(dates[i].date()) for i in (0, 4, 8)]
    assert schedule[0]['exit_date'] == schedule[1]['entry_date']
    assert schedule[1]['exit_date'] == schedule[2]['entry_date']


def test_costs_in_budget_and_both_sides_no_negative_cash():
    dates, prices, signals, config = fixture()
    config.update(buy_fee_bps=100, sell_fee_bps=100)
    result = run(prices, signals, config, dates)
    assert result['metrics']['final_equity'] == pytest.approx(1000/1.01*.99)
    assert min(row['cash'] for row in result['equity']) >= 0
    assert result['metrics']['total_fees'] == pytest.approx(1000-1000/1.01*.99)
    assert sum(t['fee'] for t in result['trades']) == pytest.approx(result['metrics']['total_fees'])


def test_slippage_applied_to_buy_and_sell_prices():
    dates, prices, signals, config = fixture()
    config['slippage_bps'] = 100
    result = run(prices, signals, config, dates)
    assert result['metrics']['final_equity'] == pytest.approx(1000/1.01*.99)
    assert result['metrics']['total_slippage'] > 0


def test_same_selection_sells_before_rebuy_and_charges_twice():
    dates, prices, signals, config = fixture()
    config.update(buy_fee_bps=100, sell_fee_bps=100)
    result = run(prices, signals, config, dates, end=4)
    assert result['metrics']['final_equity'] == pytest.approx(1000*(.99/1.01)**2)
    actions = [t['action'] for t in result['trades'] if t['date'] == str(dates[5].date())]
    assert actions == ['SELL']*5+['BUY']*5
    assert result['metrics']['cycles'] == 2
    assert result['equity'][-1]['position_value'] == 0


def test_first_loss_and_intraholding_decline_included_in_drawdown():
    dates, prices, signals, config = fixture()
    prices.loc[prices.trade_date == dates[1], 'close'] = 8.
    result = run(prices, signals, config, dates)
    assert result['metrics']['max_drawdown'] == pytest.approx(-.2)
    assert result['metrics']['drawdown_peak_date'] == str(dates[0].date())
    assert result['metrics']['drawdown_trough_date'] == str(dates[1].date())
    assert result['equity'][1]['daily_return'] == pytest.approx(-.2)


def test_month_end_marks_unrealized_positions_and_preserves_boundary_returns():
    dates, prices, signals, config = fixture()
    prices.loc[prices.trade_date == pd.Timestamp('2026-06-30'), 'close'] = 11.
    result = run(prices, signals, config, dates)
    assert result['monthly']['2026-06']['return'] == pytest.approx(.1)
    assert result['monthly']['2026-07']['return'] == pytest.approx(1000/1100-1)


def test_tied_and_negative_scores_still_pick_five_deterministically():
    dates, prices, signals, config = fixture()
    signals['raw'] = -1.
    signals = signals.sample(frac=1, random_state=3)
    result = run(prices, signals, config, dates)
    assert result['cycles'][0]['stocks'] == ['A', 'B', 'C', 'D', 'E']


@pytest.mark.parametrize('bad', ['zero', 'nan', 'duplicate', 'missing_stock'])
def test_invalid_or_incomplete_prices_fail_closed(bad):
    dates, prices, signals, config = fixture()
    if bad == 'zero':
        prices.loc[0, 'open'] = 0
    elif bad == 'nan':
        prices.loc[0, 'close'] = np.nan
    elif bad == 'duplicate':
        prices = pd.concat([prices, prices.iloc[[0]]])
    else:
        prices = prices.drop(index=0)
    with pytest.raises(ValueError, match='price|market'):
        run(prices, signals, config, dates)


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'nonfinite', 'unknown'])
def test_invalid_signal_coverage_fail_closed(bad):
    dates, prices, signals, config = fixture()
    if bad == 'missing':
        signals = signals.drop(index=0)
    elif bad == 'duplicate':
        signals = pd.concat([signals, signals.iloc[[0]]])
    elif bad == 'nonfinite':
        signals.loc[0, 'raw'] = np.nan
    else:
        signals.loc[0, 'ts_code'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='signal'):
        run(prices, signals, config, dates)


@pytest.mark.parametrize('key,value', [('initial_capital', 0), ('initial_capital', np.inf),
                                      ('buy_fee_bps', -1), ('sell_fee_bps', 10000),
                                      ('slippage_bps', np.nan), ('policy', 'unknown')])
def test_invalid_configuration_rejected(key, value):
    dates, prices, signals, config = fixture()
    config[key] = value
    with pytest.raises(ValueError, match='config'):
        run(prices, signals, config, dates)
