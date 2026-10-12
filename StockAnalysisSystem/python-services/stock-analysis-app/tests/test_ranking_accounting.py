import importlib
import numpy as np
import pandas as pd
import pytest


def inputs(n=12):
    dates = pd.bdate_range('2025-01-02', periods=n)
    prices = pd.DataFrame([dict(ts_code=c, trade_date=d, open=10., close=10.)
                           for c in ['000001.SZ', '600000.SH'] for d in dates])
    calendar = dict(source='tushare.trade_cal', exchange='SSE', start=str(dates[0].date()), end=str(dates[-1].date()),
                    rows=[dict(cal_date=str(d.date()), is_open=int(d in dates))
                          for d in pd.date_range(dates[0], dates[-1])])
    scores = prices[['ts_code', 'trade_date']].assign(pred_return=1.)
    return prices, scores, calendar


def run(prices, scores, calendar, **kwargs):
    try: module = importlib.import_module('analysis.ranking_accounting')
    except ModuleNotFoundError: pytest.fail('real ranking account missing')
    options = dict(top_n=1, rebalance_days=5, initial_capital=1000., commission_rate=0., slippage=0.)
    options.update(kwargs)
    return module.run_weighted_ranking_account(prices, scores, calendar=calendar, **options)


def test_scores_cannot_fabricate_returns_and_rebalance_is_not_daily():
    prices, scores, cal = inputs()
    first = run(prices, scores, cal)
    second = run(prices, scores.assign(pred_return=100.), cal)
    assert first['metrics']['total_return'] == second['metrics']['total_return'] == 0
    trades = first['trades']
    assert list(trades[trades.action == 'BUY'].date) == list(pd.to_datetime(['2025-01-03', '2025-01-10', '2025-01-17']))
    assert list(first['equity_curve'].date) == list(sorted(prices.trade_date.unique()))


def test_real_open_prices_drive_profit_and_costs_fit_budget():
    prices, scores, cal = inputs(7)
    prices.loc[prices.trade_date >= pd.Timestamp('2025-01-10'), ['open', 'close']] = 20.
    result = run(prices, scores, cal, commission_rate=.1)
    first_buy = result['trades'].iloc[0]
    assert first_buy['notional']+first_buy['fee'] == pytest.approx(1000)
    assert result['equity_curve'].cash.min() >= -1e-9
    # entry10, sell20: 1000/1.1 *2*.9; next buy costs another 1.1
    assert result['metrics']['final_equity'] == pytest.approx(1000/1.1*2*.9/1.1)
    assert result['metrics']['total_fees'] > 0


def test_all_nonpositive_scores_preserve_cash_periods():
    prices, scores, cal = inputs()
    result = run(prices, scores.assign(pred_return=-1.), cal)
    assert result['trades'].empty
    assert result['metrics']['total_return'] == 0
    assert len(result['trade_log']) == 3
    assert result['metrics']['max_drawdown'] == 0


def test_first_cost_loss_and_intraperiod_close_drawdown_are_counted():
    prices, scores, cal = inputs(7)
    prices.loc[prices.trade_date == pd.Timestamp('2025-01-07'), 'close'] = 5.
    result = run(prices, scores, cal, commission_rate=.1)
    assert result['metrics']['max_drawdown'] == pytest.approx(.5/1.1-1)
    assert result['metrics']['benchmark']['max_drawdown'] == pytest.approx(.5/1.1-1)


@pytest.mark.parametrize('kind', ['missing_stock', 'calendar_gap', 'score_nan', 'price_zero', 'score_gap'])
def test_incomplete_account_inputs_fail_not_skip(kind):
    prices, scores, cal = inputs()
    if kind == 'missing_stock': prices = prices.drop(index=3)
    if kind == 'calendar_gap': cal['rows'].pop(2)
    if kind == 'score_nan': scores.loc[0, 'pred_return'] = np.nan
    if kind == 'price_zero': prices.loc[0, 'open'] = 0
    if kind == 'score_gap': scores = scores[scores.trade_date != scores.trade_date.min()]
    with pytest.raises(ValueError): run(prices, scores, cal)
