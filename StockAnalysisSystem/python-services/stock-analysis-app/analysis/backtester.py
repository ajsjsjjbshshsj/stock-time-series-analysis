"""Signal research backtests: T close signal, T+1 open execution, close mark.
No board-lot, suspension, price-limit queue or minimum-commission simulation.
"""
import numpy as np
import pandas as pd


class Backtester:
    def __init__(self, initial_capital=1000000., commission_rate=.0003, slippage=.001):
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage

    def run_signal_backtest(self, df, signal_column, price_column='close',
                            date_column='trade_date', min_holding_days=1):
        config = (self.initial_capital, self.commission_rate, self.slippage)
        if (any(isinstance(v, bool) or not np.isfinite(v) for v in config)
                or self.initial_capital <= 0 or not 0 <= self.commission_rate < 1
                or not 0 <= self.slippage < 1
                or type(min_holding_days) is not int or min_holding_days < 0):
            raise ValueError('Invalid backtest capital/costs/holding period')
        if (df.empty or df.columns.duplicated().any()
                or not {date_column, price_column, 'open', signal_column}.issubset(df)):
            raise ValueError('Complete dated prices and signals required')
        frame = df.copy()
        frame[date_column] = pd.to_datetime(frame[date_column], errors='raise')
        if (frame[date_column].isna().any() or frame[date_column].duplicated().any()
                or frame[date_column].dt.tz is not None
                or not frame[date_column].equals(frame[date_column].dt.normalize())):
            raise ValueError('Invalid/duplicate price dates')
        frame = frame.sort_values(date_column).reset_index(drop=True)
        if 'ts_code' in frame and frame.ts_code.nunique() != 1:
            raise ValueError('Signal backtest requires one stock')
        for col in set(['open', price_column]):
            values = pd.to_numeric(frame[col], errors='raise')
            if not np.isfinite(values).all() or (values <= 0).any():
                raise ValueError('Invalid market prices')
            frame[col] = values
        if not frame[signal_column].isin([-1, 0, 1]).all():
            raise ValueError('Invalid trading signals')
        cash, position, held = float(self.initial_capital), 0, 0
        trades, equity = [], []
        for i, row in frame.iterrows():
            signal = frame.iloc[i-1][signal_column] if i else 0
            origin = frame.iloc[i-1][date_column] if i else None
            if signal == 1 and position == 0:
                executed = float(row['open'])*(1+self.slippage)
                shares = int(cash*.95/(executed*(1+self.commission_rate)))
                if shares > 0:
                    cost = shares*executed*(1+self.commission_rate)
                    cash -= cost
                    position, held = shares, 0
                    trades.append(dict(date=row[date_column], signal_date=origin, action='BUY',
                                       price=executed, shares=shares, cost=cost))
            elif signal == -1 and position and held >= min_holding_days:
                executed = float(row['open'])*(1-self.slippage)
                revenue = position*executed*(1-self.commission_rate)
                cash += revenue
                trades.append(dict(date=row[date_column], signal_date=origin, action='SELL',
                                   price=executed, shares=position, revenue=revenue))
                position, held = 0, 0
            if position: held += 1
            equity.append(dict(date=row[date_column], cash=cash, position=position,
                               equity=cash+position*float(row[price_column])))
        curve = pd.DataFrame(equity)
        values = curve.equity.to_numpy()
        curve['daily_return'] = values/np.r_[self.initial_capital, values[:-1]]-1
        curve['cumulative_return'] = values/self.initial_capital-1
        curve['benchmark'] = frame[price_column]/frame[price_column].iloc[0]-1
        metrics = self._calculate_metrics(curve.daily_return)
        metrics.update(total_trades=sum(t['action'] == 'BUY' for t in trades),
                       final_equity=float(values[-1]), total_return=float(values[-1]/self.initial_capital-1))
        return dict(equity_curve=curve, trades=pd.DataFrame(trades), metrics=metrics,
                    execution_assumptions=dict(signal='T close', fill='T+1 open', mark='close',
                                               allocation=.95, terminal='mark, no forced future fill',
                                               price_basis='input prices; raw prices not dividend-adjusted',
                                               commission_rate=self.commission_rate, slippage=self.slippage))

    def run_prediction_backtest(self, df, predictor, price_column='close',
                                date_column='trade_date', probability_threshold=.55,
                                min_holding_days=1, *, seen_through=None):
        from analysis.strategy_samples import require_unseen
        if predictor.model_type not in ('xgboost', 'lstm'):
            raise ValueError('Model does not produce a binary probability strategy')
        if not np.isfinite(probability_threshold) or not .5 < probability_threshold < 1:
            raise ValueError('Invalid probability threshold')
        metadata = predictor.train_metadata
        if seen_through is not None and (not metadata or metadata.get('seen_through') != seen_through):
            raise ValueError('Cannot override model dated evidence')
        frame = df.copy().sort_values(date_column)
        frame[date_column] = pd.to_datetime(frame[date_column])
        eligible = require_unseen(frame.rename(columns={date_column: 'trade_date'}), metadata)
        if eligible.empty:
            raise ValueError('No unseen evaluation dates')
        infer = predictor.prepare_inference_frame(frame)
        infer = infer[infer[date_column].isin(eligible.trade_date)]
        if infer.empty:
            raise ValueError('No valid unseen feature samples')
        if predictor.model_type == 'lstm':
            raise ValueError('LSTM requires dated sequence inference; unsupported legacy artifact')
        probabilities = np.asarray(predictor.predict_proba(infer[predictor.feature_names].to_numpy()), dtype=float)
        if (probabilities.shape != (len(infer),) or not np.isfinite(probabilities).all()
                or ((probabilities < 0) | (probabilities > 1)).any()):
            raise ValueError('Invalid binary probabilities')
        signals = pd.Series(np.where(probabilities > probability_threshold, 1,
                                     np.where(probabilities < 1-probability_threshold, -1, 0)),
                            index=infer[date_column])
        output = frame[frame[date_column].isin(eligible.trade_date)].copy()
        output['pred_signal'] = output[date_column].map(signals).fillna(0)
        return self.run_signal_backtest(output, 'pred_signal', price_column, date_column, min_holding_days)

    def _calculate_metrics(self, daily_returns):
        returns = np.asarray(daily_returns, dtype=float)
        if not np.isfinite(returns).all() or (returns <= -1).any():
            raise ValueError('Invalid realized return history')
        if not len(returns):
            return dict(annual_return=0., annual_volatility=0., sharpe_ratio=0., max_drawdown=0.,
                        win_rate=0., profit_loss_ratio=0.)
        cumulative = np.r_[1., np.cumprod(1+returns)]
        annual = cumulative[-1]**(252/len(returns))-1
        vol = np.std(returns, ddof=1)*np.sqrt(252) if len(returns) > 1 else 0.
        wins, losses = returns[returns > 0], -returns[returns < 0]
        return dict(annual_return=float(annual), annual_volatility=float(vol),
                    sharpe_ratio=float((annual-.03)/vol) if vol else 0.,
                    max_drawdown=float((cumulative/np.maximum.accumulate(cumulative)-1).min()),
                    win_rate=float((returns > 0).mean()),
                    profit_loss_ratio=float(wins.mean()/losses.mean()) if len(wins) and len(losses) else 0.)
