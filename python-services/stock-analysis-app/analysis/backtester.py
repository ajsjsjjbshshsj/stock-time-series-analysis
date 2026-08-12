"""
回测模块：基于预测信号模拟交易，计算策略收益与风险指标
"""
import numpy as np
import pandas as pd
from config.logging_config import get_logger
logger = get_logger(__name__)


class Backtester:
    """策略回测器"""

    def __init__(self, initial_capital=1000000.0, commission_rate=0.0003, slippage=0.001):
        """
        初始化回测器

        Args:
            initial_capital: 初始资金
            commission_rate: 佣金率（单边）
            slippage: 滑点
        """
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.slippage = slippage
        logger.info("回测器初始化完成")

    # =========================================================================
    # 信号驱动回测
    # =========================================================================
    def run_signal_backtest(self, df, signal_column, price_column='close',
                            date_column='trade_date', min_holding_days=1):
        """
        基于交易信号的回测（信号列中 1=买入，-1=卖出，0=持有）

        Args:
            df: 包含信号和价格的DataFrame（按时间排序）
            signal_column: 信号列名
            price_column: 价格列名
            date_column: 日期列名
            min_holding_days: 最小持有天数

        Returns:
            dict: 回测结果
        """
        df = df.copy().sort_values(date_column).reset_index(drop=True)

        capital = self.initial_capital
        position = 0  # 持仓股数
        holding_days = 0
        trades = []
        equity_curve = []
        daily_returns = []

        for i in range(len(df)):
            row = df.iloc[i]
            price = row[price_column]
            signal = row.get(signal_column, 0)
            date = row[date_column]

            # 买入信号
            if signal == 1 and position == 0 and holding_days >= min_holding_days:
                # 扣除滑点和佣金
                buy_price = price * (1 + self.slippage)
                shares = int(capital / buy_price * 0.95)  # 用95%仓位
                if shares > 0:
                    cost = shares * buy_price * (1 + self.commission_rate)
                    capital -= cost
                    position = shares
                    holding_days = 0
                    trades.append({
                        'date': date,
                        'action': 'BUY',
                        'price': buy_price,
                        'shares': shares,
                        'cost': cost
                    })

            # 卖出信号
            elif signal == -1 and position > 0 and holding_days >= min_holding_days:
                sell_price = price * (1 - self.slippage)
                revenue = position * sell_price * (1 - self.commission_rate)
                capital += revenue
                trades.append({
                    'date': date,
                    'action': 'SELL',
                    'price': sell_price,
                    'shares': position,
                    'revenue': revenue
                })
                position = 0
                holding_days = 0

            holding_days += 1

            # 计算当日权益
            total_equity = capital + position * price
            equity_curve.append({'date': date, 'equity': total_equity})
            daily_returns.append(total_equity)

        # 计算策略收益序列
        equity_df = pd.DataFrame(equity_curve)
        if len(equity_df) > 1:
            equity_df['daily_return'] = equity_df['equity'].pct_change()
            equity_df['cumulative_return'] = (equity_df['equity'] / self.initial_capital) - 1
            equity_df['benchmark'] = df[price_column] / df[price_column].iloc[0] - 1
        else:
            equity_df['daily_return'] = 0
            equity_df['cumulative_return'] = 0
            equity_df['benchmark'] = 0

        metrics = self._calculate_metrics(equity_df['daily_return'].dropna())
        metrics['total_trades'] = len([t for t in trades if t['action'] == 'BUY'])
        metrics['final_equity'] = equity_df['equity'].iloc[-1] if len(equity_df) > 0 else self.initial_capital
        metrics['total_return'] = (metrics['final_equity'] / self.initial_capital) - 1

        logger.info(f"回测完成: 总收益={metrics['total_return']:.2%}, "
                     f"交易次数={metrics['total_trades']}, 夏普={metrics['sharpe_ratio']:.3f}")

        return {
            'equity_curve': equity_df,
            'trades': pd.DataFrame(trades) if trades else pd.DataFrame(),
            'metrics': metrics
        }

    # =========================================================================
    # 预测驱动回测（使用模型预测生成信号）
    # =========================================================================
    def run_prediction_backtest(self, df, predictor, price_column='close',
                                date_column='trade_date', probability_threshold=0.55,
                                min_holding_days=1):
        """
        基于模型预测的回测

        Args:
            df: 原始数据DataFrame
            predictor: 已训练的StockPredictor实例
            price_column: 价格列名
            date_column: 日期列名
            probability_threshold: 预测概率阈值（高于此值才买入）
            min_holding_days: 最小持有天数

        Returns:
            dict: 回测结果
        """
        # 准备特征并生成预测
        X, y, features = predictor.prepare_features(df)
        if X is None:
            logger.error("特征准备失败，无法回测")
            return None

        predictions = predictor.predict(X)

        # 将预测结果对齐回原始DataFrame
        df_pred = df.iloc[-len(predictions):].copy()
        df_pred['pred_signal'] = 0
        df_pred.loc[predictions == 1, 'pred_signal'] = 1
        df_pred.loc[predictions == 0, 'pred_signal'] = -1

        return self.run_signal_backtest(df_pred, 'pred_signal', price_column, date_column, min_holding_days)

    # =========================================================================
    # 指标计算
    # =========================================================================
    def _calculate_metrics(self, daily_returns):
        """
        计算回测指标

        Args:
            daily_returns: 日收益率Series

        Returns:
            dict: 指标字典
        """
        if daily_returns.empty or daily_returns.std() == 0:
            return {
                'annual_return': 0,
                'annual_volatility': 0,
                'sharpe_ratio': 0,
                'max_drawdown': 0,
                'win_rate': 0,
                'profit_loss_ratio': 0
            }

        # 年化收益率
        annual_return = (1 + daily_returns.mean()) ** 252 - 1

        # 年化波动率
        annual_vol = daily_returns.std() * np.sqrt(252)

        # 夏普比率（无风险利率3%）
        sharpe_ratio = (annual_return - 0.03) / annual_vol if annual_vol > 0 else 0

        # 最大回撤
        cumulative = (1 + daily_returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        max_drawdown = drawdown.min()

        # 胜率
        wins = (daily_returns > 0).sum()
        total = len(daily_returns)
        win_rate = wins / total if total > 0 else 0

        # 盈亏比
        avg_win = daily_returns[daily_returns > 0].mean() if (daily_returns > 0).any() else 0
        avg_loss = abs(daily_returns[daily_returns < 0].mean()) if (daily_returns < 0).any() else 1
        profit_loss_ratio = avg_win / avg_loss if avg_loss != 0 else 0

        return {
            'annual_return': annual_return,
            'annual_volatility': annual_vol,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'profit_loss_ratio': profit_loss_ratio
        }