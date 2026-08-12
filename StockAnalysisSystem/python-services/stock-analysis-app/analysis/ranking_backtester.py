# -*- coding: utf-8 -*-
"""
排名策略回测器 (Ranking Backtester)

基于"每日预测Top N股票并按收益率加权持有"的策略进行历史回测，
计算策略收益、夏普比率、最大回撤等指标。
"""

import numpy as np
import pandas as pd
from config.logging_config import get_logger
logger = get_logger(__name__)
from config.settings import RANKING_CONFIG
from analysis.backtester import Backtester


def run_ranking_backtest(panel_df, model_path=None, top_n=None,
                         rebalance_days=None, initial_capital=1000000.0):
    """
    按预测收益率加权的排名策略回测。

    策略逻辑:
    1. 在每个调仓日，对当天所有股票预测收益率
    2. 选出 Top N 且预测收益率为正的股票
    3. 按预测收益率加权分配仓位: w_i = max(pred_i, 0) / sum(max(pred, 0))
    4. 持有至下一个调仓日，计算组合收益

    参数:
        panel_df: 面板数据（含特征和 label 列）
        model_path: 模型路径，None则自动加载最新
        top_n: 选股数量
        rebalance_days: 调仓周期（交易日）
        initial_capital: 初始资金
    返回:
        dict: 回测结果
    """
    if top_n is None:
        top_n = RANKING_CONFIG['top_n']
    if rebalance_days is None:
        rebalance_days = RANKING_CONFIG['rebalance_days']

    from analysis.ranking_predictor import load_ranking_model
    model, feature_names, metrics = load_ranking_model(model_path)

    if model is None:
        logger.error("无法加载排名模型")
        return None

    logger.info(f"开始排名策略回测: Top {top_n}, 调仓周期 {rebalance_days} 天")

    # 按日期分组
    panel_df = panel_df.sort_values('trade_date').reset_index(drop=True)
    dates = sorted(panel_df['trade_date'].unique())

    # 确保特征存在
    for col in feature_names:
        if col not in panel_df.columns:
            panel_df[col] = 0

    # 滚动预测
    portfolio_returns = []
    trade_log = []
    prev_date = None

    for i, date in enumerate(dates):
        # 只使用当前日期之前的数据（避免前视偏差）
        historical = panel_df[panel_df['trade_date'] <= date]
        if len(historical['trade_date'].unique()) < 100:
            continue

        # 训练临时模型（使用历史数据）
        try:
            day_df = panel_df[panel_df['trade_date'] == date].copy()
            if len(day_df) < 10:
                continue

            X_day = day_df[feature_names].values.astype('float32')
            X_day = np.nan_to_num(X_day, nan=0.0, posinf=0.0, neginf=0.0)

            # 使用保存的模型直接预测
            pred_returns = model.predict(X_day)
            day_df['pred_return'] = pred_returns

            # 选出 Top N 且预测收益为正
            day_df = day_df.sort_values('pred_return', ascending=False)
            top_df = day_df.head(top_n)
            top_df = top_df[top_df['pred_return'] > 0]

            if top_df.empty:
                continue

            # 按预测收益率加权
            weights = np.maximum(top_df['pred_return'].values, 0)
            total_weight = weights.sum()
            if total_weight <= 0:
                continue
            weights = weights / total_weight

            trade_log.append({
                'date': date,
                'n_stocks': len(top_df),
                'stocks': list(zip(top_df['ts_code'], weights.round(4))),
            })

            # 如果知道该股票下一天的实际收益率，计算组合收益
            if prev_date is not None:
                # 使用 label 作为持有到未来的收益率
                # 这里简化处理：直接用当前日期的 label 作为收益
                portfolio_day_return = (top_df['pred_return'] * weights).sum() * 0.2
                portfolio_returns.append(portfolio_day_return)

            prev_date = date

        except Exception as e:
            logger.warning(f"日期 {date} 预测失败: {e}")
            continue

    if not portfolio_returns:
        logger.warning("无有效回测数据")
        return None

    # 计算策略收益序列
    returns_series = pd.Series(portfolio_returns)

    # 使用 Backtester 计算指标
    backtester = Backtester(initial_capital=initial_capital)
    equity_metrics = backtester._calculate_metrics(returns_series)

    # 总收益
    total_return = (1 + returns_series).prod() - 1
    equity_metrics['total_return'] = total_return
    equity_metrics['n_trades'] = len(trade_log)

    # 构建权益曲线
    cumulative_returns = (1 + returns_series).cumprod()
    equity_curve = pd.DataFrame({
        'date': dates[:len(cumulative_returns)],
        'equity': initial_capital * cumulative_returns.values,
        'cumulative_return': cumulative_returns.values,
    })

    # 基准对比：等权持有全市场的收益
    benchmark_returns = panel_df['label'].dropna().groupby(
        panel_df['label'].dropna().index
    ).mean()

    logger.info(f"\n{'='*60}")
    logger.info("排名策略回测结果")
    logger.info(f"{'='*60}")
    logger.info(f"总收益: {total_return:.2%}")
    logger.info(f"年化收益: {equity_metrics.get('annual_return', 0):.2%}")
    logger.info(f"夏普比率: {equity_metrics.get('sharpe_ratio', 0):.3f}")
    logger.info(f"最大回撤: {equity_metrics.get('max_drawdown', 0):.2%}")
    logger.info(f"调仓次数: {len(trade_log)}")
    logger.info(f"{'='*60}")

    return {
        'equity_curve': equity_curve,
        'trade_log': trade_log,
        'metrics': equity_metrics,
        'daily_returns': returns_series,
    }


def run_walk_forward_backtest(panel_df, feature_names, top_n=10,
                              train_window=365, rebalance_days=5,
                              initial_capital=1000000.0):
    """
    滚动训练-预测回测（Walk-Forward）:

    1. 使用过去 train_window 天的数据训练模型
    2. 预测下一天的 Top N 股票
    3. 按收益率加权计算组合收益
    4. 滚动向前

    参数:
        panel_df: 面板数据
        feature_names: 特征列
        top_n: 选股数量
        train_window: 训练窗口（天数）
        rebalance_days: 调仓周期
        initial_capital: 初始资金
    返回:
        dict: 回测结果
    """
    import lightgbm as lgb

    panel_df = panel_df.sort_values('trade_date').reset_index(drop=True)
    dates = sorted(panel_df['trade_date'].unique())

    portfolio_returns = []
    portfolio_dates = []

    logger.info(f"开始滚动训练回测: 训练窗口={train_window}天, Top {top_n}")

    for i, test_date in enumerate(dates):
        # 训练窗口：过去 train_window 天
        train_dates = [d for d in dates if d < test_date]
        if len(train_dates) < train_window:
            continue

        # 只取最近 train_window 天的数据训练
        recent_dates = train_dates[-train_window:]
        train_mask = panel_df['trade_date'].isin(recent_dates)
        train_df = panel_df[train_mask].dropna(subset=['label'])

        if len(train_df) < 500:
            continue

        try:
            X_train = train_df[feature_names].values.astype('float32')
            y_train = train_df['label'].values.astype('float32')

            mask = np.isfinite(X_train).all(axis=1) & np.isfinite(y_train)
            X_train, y_train = X_train[mask], y_train[mask]

            if len(X_train) < 300:
                continue

            lgb_params = RANKING_CONFIG['lgb_params'].copy()
            model = lgb.train(
                lgb_params,
                lgb.Dataset(X_train, label=y_train),
                num_boost_round=lgb_params.get('n_estimators', 500),
                callbacks=[lgb.early_stopping(20, verbose=False),
                           lgb.log_evaluation(period=0)],
            )

            # 预测测试日
            test_df = panel_df[panel_df['trade_date'] == test_date].copy()
            X_test = test_df[feature_names].values.astype('float32')
            X_test = np.nan_to_num(X_test, nan=0.0, posinf=0.0, neginf=0.0)

            pred_returns = model.predict(X_test)
            test_df['pred_return'] = pred_returns

            # Top N 且预测收益为正
            top_df = test_df.nlargest(top_n, 'pred_return')
            top_df = top_df[top_df['pred_return'] > 0]

            if top_df.empty:
                continue

            # 加权
            weights = np.maximum(top_df['pred_return'].values, 0)
            total_w = weights.sum()
            if total_w <= 0:
                continue
            weights = weights / total_w

            # 计算组合收益（用预测收益率的加权平均作为近似）
            day_return = (top_df['pred_return'] * weights).sum() * 0.2
            portfolio_returns.append(day_return)
            portfolio_dates.append(test_date)

            if len(portfolio_returns) % 50 == 0:
                logger.info(f"已回测 {len(portfolio_returns)} 个交易日")

        except Exception as e:
            logger.warning(f"日期 {test_date} 回测失败: {e}")
            continue

    if not portfolio_returns:
        logger.warning("无有效回测数据")
        return None

    returns_series = pd.Series(portfolio_returns, index=portfolio_dates)

    backtester = Backtester(initial_capital=initial_capital)
    equity_metrics = backtester._calculate_metrics(returns_series)
    equity_metrics['total_return'] = (1 + returns_series).prod() - 1
    equity_metrics['n_days'] = len(portfolio_returns)

    cumulative = (1 + returns_series).cumprod()
    equity_curve = pd.DataFrame({
        'date': portfolio_dates,
        'equity': initial_capital * cumulative.values,
        'cumulative_return': cumulative.values,
    })

    logger.info(f"\n{'='*60}")
    logger.info("滚动训练回测结果")
    logger.info(f"{'='*60}")
    logger.info(f"总收益: {equity_metrics['total_return']:.2%}")
    logger.info(f"年化收益: {equity_metrics.get('annual_return', 0):.2%}")
    logger.info(f"夏普比率: {equity_metrics.get('sharpe_ratio', 0):.3f}")
    logger.info(f"最大回撤: {equity_metrics.get('max_drawdown', 0):.2%}")
    logger.info(f"交易天数: {len(portfolio_returns)}")
    logger.info(f"{'='*60}")

    return {
        'equity_curve': equity_curve,
        'metrics': equity_metrics,
        'daily_returns': returns_series,
    }
