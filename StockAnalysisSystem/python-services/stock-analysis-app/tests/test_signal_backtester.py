import pandas as pd
import pytest

from analysis.backtester import Backtester


def bars(signals=(1, 0, -1, 0)):
    return pd.DataFrame(dict(trade_date=pd.bdate_range('2025-01-01', periods=len(signals)),
                             open=[10., 20., 30., 40.][:len(signals)],
                             close=[11., 21., 31., 41.][:len(signals)], signal=signals))


def test_close_signal_executes_only_next_open_and_first_buy_is_allowed():
    result = Backtester(1000, 0, 0).run_signal_backtest(bars(), 'signal')
    assert result['trades'].iloc[0]['date'] == pd.Timestamp('2025-01-02')
    assert result['trades'].iloc[0]['price'] == 20.
    assert result['trades'].iloc[1]['date'] == pd.Timestamp('2025-01-06')
    assert result['trades'].iloc[1]['price'] == 40.


def test_terminal_signal_cannot_create_a_fill():
    result = Backtester(1000, 0, 0).run_signal_backtest(bars((0, 0, 0, 1)), 'signal')
    assert result['trades'].empty
    assert result['metrics']['total_return'] == 0


def test_cash_budget_includes_fees_and_initial_loss_is_drawdown():
    frame = bars((1, 0)).assign(open=10., close=10.)
    result = Backtester(1000, .1, 0).run_signal_backtest(frame, 'signal')
    buy = result['trades'].iloc[0]
    assert buy['cost'] <= 1000
    assert result['metrics']['max_drawdown'] == pytest.approx(result['metrics']['total_return'])
    assert result['metrics']['max_drawdown'] < 0


@pytest.mark.parametrize('kind', ['empty', 'zero', 'duplicate', 'missing_open', 'bad_signal'])
def test_malformed_market_input_fails_instead_of_fake_results(kind):
    frame = bars()
    if kind == 'empty': frame = frame.iloc[:0]
    if kind == 'zero': frame.loc[1, 'open'] = 0
    if kind == 'duplicate': frame.loc[1, 'trade_date'] = frame.loc[0, 'trade_date']
    if kind == 'missing_open': frame = frame.drop(columns='open')
    if kind == 'bad_signal': frame.loc[1, 'signal'] = 7
    with pytest.raises(ValueError):
        Backtester().run_signal_backtest(frame, 'signal')


def test_holding_period_does_not_delay_first_entry():
    result = Backtester(1000, 0, 0).run_signal_backtest(bars((1, -1, -1, 0)), 'signal', min_holding_days=2)
    assert result['trades'].iloc[0]['date'] == pd.Timestamp('2025-01-02')
    assert result['trades'].iloc[1]['date'] == pd.Timestamp('2025-01-06')


@pytest.mark.parametrize('capital,fee,slip', [(0, 0, 0), (1000, -1, 0), (1000, 0, 1)])
def test_invalid_financial_config_rejected(capital, fee, slip):
    with pytest.raises(ValueError):
        Backtester(capital, fee, slip).run_signal_backtest(bars(), 'signal')
