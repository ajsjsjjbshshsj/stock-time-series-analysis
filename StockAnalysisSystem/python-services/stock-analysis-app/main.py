"""
项目总入口 - 调度器
"""
import argparse
import os
import subprocess
import sys
from datetime import datetime
import json
import numpy as np
import pandas as pd
from config.logging_config import get_logger
logger = get_logger(__name__)
from data_loader.collector import DataCollector
from data_processor.cleaner import DataCleaner
from data_processor.feature_engineer import FeatureEngineer
from database.db_connector import DatabaseConnector
from database import repository
from analysis.statistical import StatisticalAnalyzer
from analysis.predictor import StockPredictor
from analysis.backtester import Backtester
from analysis.ranking_predictor import train_ranking_model, predict_top_n
from analysis.ranking_backtester import run_walk_forward_backtest
from visualization.plotter import StockPlotter


def _safe_path_part(value):
    value = str(value).strip()
    invalid_chars = '<>:"/\\|?*'
    cleaned = ''.join('_' if ch in invalid_chars or ch.isspace() else ch for ch in value)
    cleaned = cleaned.strip('._')
    return cleaned or 'unknown'


def build_transformer_config(args, sector_list=None, index_list=None):
    """Build Transformer config with an output directory scoped by feature set and stock universe."""
    from analysis.transformer_config import TRANSFORMER_CONFIG

    t_config = TRANSFORMER_CONFIG.copy()
    t_config['use_multi_head'] = (args.transformer_model == 'multi_head')
    t_config['feature_num'] = args.transformer_features
    if args.transformer_epochs:
        t_config['num_epochs'] = args.transformer_epochs
    if args.transformer_lr:
        t_config['learning_rate'] = args.transformer_lr
    if args.use_sector_filter:
        t_config['use_sector_filter'] = True

    transformer_root = os.path.dirname(TRANSFORMER_CONFIG['output_dir'])
    base_dir = os.path.join(
        transformer_root,
        f"{t_config['sequence_length']}_{t_config['feature_num']}",
    )

    scope_parts = []
    if index_list:
        scope_parts.append('index_' + '_'.join(_safe_path_part(i) for i in index_list))
    if sector_list:
        scope_parts.append('sectors_' + '_'.join(_safe_path_part(s) for s in sector_list))
    if scope_parts:
        base_dir = os.path.join(base_dir, '__'.join(scope_parts))

    t_config['output_dir'] = base_dir
    return t_config


def run_data_collection(stock_codes=None, use_tushare=False, delay=0.5, start_date=None):
    """
    执行数据采集流程：增量更新，只采集新数据并写入数据库。

    Args:
        stock_codes: 股票代码列表
        use_tushare: 是否使用Tushare
        delay: 请求间隔秒数
        start_date: 起始日期，格式 YYYYMMDD
    """
    logger.info("="*50)
    logger.info("读取 python-collector 已落库的市场数据")
    if use_tushare:
        logger.info("--use_tushare 已弃用，分析端统一读取 MySQL")
    logger.info("="*50)

    from data_processor.panel_builder import incremental_update

    panel_df = incremental_update(stock_codes=stock_codes, use_tushare=use_tushare, delay=delay, start_date=start_date)

    if panel_df.empty:
        logger.error("数据库读取结果为空。请检查:")
        logger.error("  1. .env 数据库配置是否正确")
        logger.error("  2. MySQL 服务是否运行")
        logger.error("  3. python-collector 是否已完成行情采集")
        logger.error("  4. 查看上方日志中的具体失败原因")
    else:
        logger.info(f"数据加载完成，共 {len(panel_df)} 条记录, {panel_df['ts_code'].nunique()} 只股票")

    return panel_df


def run_data_processing(df, stock_code="unknown"):
    """
    执行数据处理流程
    
    Args:
        df: 原始数据DataFrame
        stock_code: 股票代码
        
    Returns:
        DataFrame: 处理后的数据
    """
    logger.info("="*50)
    logger.info("开始数据处理流程")
    logger.info("="*50)
    
    # 清洗数据
    cleaner = DataCleaner()
    df_cleaned = cleaner.clean_stock_data(df, stock_code)
    
    # 特征工程
    engineer = FeatureEngineer()
    df_featured = engineer.calculate_all_features(df_cleaned)
    
    logger.info("数据处理完成")
    return df_featured


def run_analysis(df, stock_code="unknown"):
    """
    执行分析流程
    
    Args:
        df: 处理后数据
        stock_code: 股票代码
    """
    logger.info("="*50)
    logger.info("开始分析流程")
    logger.info("="*50)
    
    analyzer = StatisticalAnalyzer()
    
    # 描述性统计
    logger.info("\n描述性统计:")
    stats = analyzer.descriptive_stats(df)
    print(stats)
    
    # 趋势分析
    trend = analyzer.trend_analysis(df, 'close')
    logger.info(f"\n趋势分析结果: {trend}")
    
    # 风险指标
    risk = analyzer.risk_metrics(df, 'close')
    logger.info(f"\n风险指标: {risk}")
    
    # 可视化
    plotter = StockPlotter()
    plotter.plot_candlestick(df, title=f"{stock_code} K线图")
    plotter.plot_technical_indicators(df, title=f"{stock_code} 技术指标")
    
    logger.info("分析流程完成")


def run_prediction(df, model_type='xgboost'):
    """
    执行预测流程

    Args:
        df: 处理后数据
        model_type: 模型类型 ('xgboost', 'xgboost_regression', 'xgboost_multiclass', 'lstm')

    Returns:
        dict: 预测结果
    """
    if model_type == 'lstm':
        raise ValueError('LSTM training is not implemented; no substitute model will be trained')
    logger.info("="*50)
    logger.info(f"开始预测流程 ({model_type})")
    logger.info("="*50)

    predictor = StockPredictor(model_type=model_type)

    # 根据模型类型选择目标列
    if model_type in ('xgboost_regression', 'xgboost_multiclass'):
        target_column = 'future_return_1d'
    else:
        target_column = 'future_direction_1d'

    X, y, feature_names = predictor.prepare_features(df, target_column=target_column)

    if X is None:
        logger.error("特征准备失败")
        return None

    # 训练模型
    if model_type == 'xgboost':
        result = predictor.train_xgboost(X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
    elif model_type == 'xgboost_regression':
        result = predictor.train_xgboost_regression(X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
    elif model_type == 'xgboost_multiclass':
        result = predictor.train_xgboost_multiclass(X, y, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
    elif model_type == 'lstm':
        result = predictor.train_lstm(X, y, sequence_length=60, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2)
    else:
        logger.error(f"不支持的模型类型: {model_type}")
        return None

    if result:
        # 可视化预测结果
        plotter = StockPlotter()
        plotter.plot_prediction_results(
            result['y_test'],
            result['predictions'],
            title=f"{model_type.upper()}预测结果"
        )

        # 特征重要性（仅XGBoost）
        if model_type.startswith('xgboost') and feature_names:
            importance = predictor.feature_importance(feature_names)
            if importance is not None:
                plotter.plot_feature_importance(importance)

        # 保存模型
        predictor.save_model(f"{model_type}_latest")

        # 最新预测
        latest_X = X[-1:].reshape(1, -1) if X.ndim == 2 else X[-1:]
        if model_type == 'xgboost_regression':
            pred_return = predictor.predict_regression(latest_X)
            if pred_return is not None:
                logger.info(f"最新预测收益率: {pred_return[0]:.4%}")
        elif model_type == 'xgboost_multiclass':
            pred_class = predictor.predict_multiclass(latest_X)
            proba = predictor.predict_multiclass_proba(latest_X)
            if pred_class is not None:
                labels = result.get('labels', ['大幅跌', '小跌', '平', '小涨', '大涨'])
                logger.info(f"最新预测类别: {labels[pred_class[0]]}")
                logger.info(f"各类别概率: {dict(zip(labels, proba[0].round(3)))}")

        logger.info("预测流程完成")

    return result


def load_strategy_calendar(calendar):
    """Explicit local trusted calendar; no network or weekday fallback."""
    from analysis.strategy_dates import validate_session_calendar
    if calendar is None:
        raise ValueError('A trusted --calendar is required for strategy validation')
    if not isinstance(calendar, dict):
        with open(calendar, encoding='utf-8') as stream:
            calendar = json.load(stream)
    validate_session_calendar(calendar)
    return calendar


def select_strategy_snapshot(frame, calendar, request_time=None, *, stock_codes=None,
                             required_columns=('open', 'high', 'low', 'close', 'vol', 'amount')):
    from analysis.strategy_dates import select_data_cutoff, SHANGHAI
    from analysis.ranking_accounting import validate_ranking_history
    calendar = load_strategy_calendar(calendar)
    if frame.empty or 'ts_code' not in frame:
        raise ValueError('Dated stock pool required')
    expected = stock_codes if stock_codes is not None else frame.attrs.get('requested_codes')
    if not expected:
        raise ValueError('Independent requested stock pool evidence required')
    selected = select_data_cutoff(calendar, request_time or datetime.now(SHANGHAI), frame,
                                  expected, required_columns=required_columns)
    result = frame[pd.to_datetime(frame.trade_date) <= pd.Timestamp(selected['data_cutoff'])].copy()
    result['trade_date'] = pd.to_datetime(result.trade_date)
    validate_ranking_history(result, calendar)
    logger.info('目标交易日=%s 实际截止=%s 回退原因=%s', selected['target_trade_date'],
                selected['data_cutoff'], selected['fallback_reason'])
    return result, selected


def run_backtest(df, stock_code="unknown", *, calendar=None, request_time=None, model_type='xgboost'):
    """Validated single-stock technical and dated XGBoost research accounts."""
    if model_type != 'xgboost':
        raise ValueError('Validated probability backtest requires binary xgboost')
    code = str(stock_code)
    if len(code) == 6 and code.isdigit():
        code += '.SH' if code.startswith('6') else '.SZ'
    raw, selected = select_strategy_snapshot(df, calendar, request_time, stock_codes=[code])
    if raw.ts_code.nunique() != 1:
        raise ValueError('Single-stock strategy needs one stock')
    signals = raw.copy()
    signals['signal'] = 0
    for field, value in [('golden_cross', 1), ('death_cross', -1),
                         ('rsi_overbought', -1), ('rsi_oversold', 1)]:
        if field in signals:
            signals.loc[signals[field] == 1, 'signal'] = value
    if not any(c in signals for c in ('golden_cross', 'death_cross', 'rsi_overbought', 'rsi_oversold')):
        raise ValueError('Technical strategy signal features missing')
    backtester = Backtester()
    tech = backtester.run_signal_backtest(signals, 'signal')
    predictor = StockPredictor('xgboost')
    names = [c for c in ('ma5', 'ma10', 'rsi', 'macd_dif', 'vol') if c in raw]
    training = predictor.train_dated(raw, feature_names=names)
    prediction = backtester.run_prediction_backtest(raw, predictor)
    return dict(technical=tech, prediction=prediction, model_metadata=training['metadata'],
                data_selection=selected)


def save_data_to_db(stock_code, df_raw, df_processed, prediction_result=None, model_type=None):
    """
    将采集、处理、预测结果写入数据库

    Args:
        stock_code: 股票代码
        df_raw: 原始数据DataFrame
        df_processed: 处理后数据DataFrame
        prediction_result: 预测结果字典
        model_type: 模型类型
    """
    logger.info("开始保存数据到数据库...")

    with DatabaseConnector() as db:
        with db.session_scope() as session:
            # 1. 保存日线数据 (stock_daily)
            daily_records = []
            for _, row in df_raw.iterrows():
                trade_date = row.get('trade_date', row.get('date'))
                if pd.isna(trade_date):
                    continue
                if not isinstance(trade_date, pd.Timestamp):
                    trade_date = pd.to_datetime(trade_date)
                daily_records.append({
                    'ts_code': stock_code,
                    'trade_date': trade_date,
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'pre_close': float(row['pre_close']) if 'pre_close' in row else None,
                    'change': float(row['change']) if 'change' in row else None,
                    'pct_chg': float(row['pct_chg']) if 'pct_chg' in row else None,
                    'vol': float(row.get('vol', 0)),
                    'amount': float(row['amount']) if 'amount' in row else None,
                })

            if daily_records:
                repository.save_daily_records(session, daily_records)
            logger.info(f"保存 {len(daily_records)} 条日线数据")

            # 2. 保存技术指标 (stock_features)
            # feature_engineer v2.0 输出 snake_case，映射与 DB 列名 1:1 对齐
            FEATURE_MAP = {
                'ma5': 'ma5', 'ma10': 'ma10', 'ma20': 'ma20', 'ma60': 'ma60',
                'ema5': 'ema5', 'ema10': 'ema10', 'ema20': 'ema20',
                'ema26': 'ema26', 'ema60': 'ema60', 'ema120': 'ema120',
                'macd_dif': 'macd_dif', 'macd_dea': 'macd_dea', 'macd_hist': 'macd_hist',
                'rsi': 'rsi',
                'bb_upper': 'bb_upper', 'bb_middle': 'bb_middle',
                'bb_lower': 'bb_lower', 'bb_width': 'bb_width',
                'kdj_k': 'kdj_k', 'kdj_d': 'kdj_d', 'kdj_j': 'kdj_j',
                'vol_ma5': 'vol_ma5', 'vol_ma10': 'vol_ma10',
                'volume_ratio': 'volume_ratio', 'mfi14': 'mfi14',
                'return_1d': 'return_1d', 'return_5d': 'return_5d',
                'return_10d': 'return_10d', 'log_return': 'log_return',
                # [防泄漏] future_return / future_direction 不入库
                # 仅作为训练标签，由 pipeline 在划分训练集时临时计算
                'turnover_rate_5': 'turnover_rate_5',
                'turnover_rate_60': 'turnover_rate_60',
                'turnover_rate_120': 'turnover_rate_120',
                'br': 'br', 'ar': 'ar',
                'volatility_20d': 'volatility_20d', 'volatility_60d': 'volatility_60d',
                'volatility_120d': 'volatility_120d',
                'skewness_20d': 'skewness_20d', 'skewness_60d': 'skewness_60d',
                'skewness_120d': 'skewness_120d',
                'kurtosis_20d': 'kurtosis_20d', 'kurtosis_60d': 'kurtosis_60d',
                'kurtosis_120d': 'kurtosis_120d',
                'arron_up_25': 'arron_up_25', 'arron_down_25': 'arron_down_25',
                'bear_power': 'bear_power', 'bull_power': 'bull_power',
                'bias5': 'bias5', 'bias10': 'bias10', 'bias20': 'bias20', 'bias60': 'bias60',
                'cci10': 'cci10', 'cci15': 'cci15', 'cci20': 'cci20', 'cci88': 'cci88',
                'cr20': 'cr20', 'mass': 'mass',
                'golden_cross': 'golden_cross', 'death_cross': 'death_cross',
                'macd_golden_cross': 'macd_golden_cross',
                'rsi_oversold': 'rsi_oversold', 'rsi_overbought': 'rsi_overbought',
            }

            available = {df_col: db_col for df_col, db_col in FEATURE_MAP.items()
                         if df_col in df_processed.columns}

            feature_records = []
            for _, row in df_processed.iterrows():
                trade_date = row.get('trade_date', row.get('date'))
                if pd.isna(trade_date):
                    continue
                if not isinstance(trade_date, pd.Timestamp):
                    trade_date = pd.to_datetime(trade_date)

                record = {'ts_code': stock_code, 'trade_date': trade_date}
                has_nan = False
                for df_col, db_col in available.items():
                    v = row.get(df_col)
                    if v is None or pd.isna(v):
                        has_nan = True
                        break
                    record[db_col] = float(v)

                if has_nan:
                    continue
                feature_records.append(record)

            if feature_records:
                db_cols = list(available.values())
                repository.save_features(session, feature_records, db_cols)
            logger.info(f"保存 {len(feature_records)} 条特征数据")

            # 3. 保存分析结果 (analysis_result)
            if prediction_result and model_type:
                metrics = prediction_result.get('metrics', {})
                repository.save_analysis_result(session, {
                    'ts_code': stock_code,
                    'analysis_date': pd.Timestamp.now().date(),
                    'analysis_type': f'{model_type}_prediction',
                    'result': str(metrics),
                    'prediction': metrics.get('Accuracy', 0),
                    'confidence': metrics.get('F1', 0),
                })
                logger.info("保存分析结果")

    logger.info("数据保存完成！")


def init_database():
    """初始化数据库"""
    logger.info("初始化数据库...")
    # 导入模型，使 Base.metadata 注册表定义
    import database.models  # noqa: F401
    with DatabaseConnector() as db:
        db.create_tables()
    logger.info("数据库初始化完成")


def run_ranking_pipeline(top_n=10, use_probe=True, forward_days=5, use_tushare=False,
                         skip_update=False, delay=.5, use_gpu=False, n_workers=None,
                         sectors=None, index_codes=None, *, calendar=None, request_time=None):
    from data_processor.panel_builder import incremental_update, prepare_panel_for_training, load_all_stock_data_from_db
    from analysis.ranking_predictor import get_feature_columns
    calendar = load_strategy_calendar(calendar)  # Reject missing calendar before DB.
    # Validated analysis reads strict DB sources only; collector owns updates.
    raw = load_all_stock_data_from_db(sectors=sectors, index_codes=index_codes,
                                      use_tushare=use_tushare, strict_validation=True)
    required = tuple(dict.fromkeys(['open', 'high', 'low', 'close', 'vol', 'amount', 'turnover_rate']
                                  + get_feature_columns(raw)))
    raw, selection = select_strategy_snapshot(raw, calendar, request_time, required_columns=required)
    featured = prepare_panel_for_training(raw, forward_days=forward_days, use_tushare=use_tushare,
                                          n_workers=n_workers, use_parallel=True, keep_unlabelled=True)
    from analysis.ranking_backtester import require_panel_coverage
    require_panel_coverage(featured, raw)
    result = train_ranking_model(featured, use_probe=use_probe, forward_days=forward_days, use_gpu=use_gpu)
    stocks = predict_top_n(featured, model_path=result['model_path'], top_n=top_n)
    stocks.attrs['data_selection'] = selection
    stocks.attrs['model_metadata'] = result.get('metadata', {})
    return stocks


def run_ranking_backtest_pipeline(top_n=10, use_probe=True, forward_days=5,
                                  train_window=365, rebalance_days=5, use_tushare=False,
                                  delay=.5, skip_update=False, use_gpu=False, n_workers=None,
                                  sectors=None, index_codes=None, *, calendar=None, request_time=None):
    from data_processor.panel_builder import incremental_update, prepare_panel_for_training, load_all_stock_data_from_db
    from analysis.ranking_predictor import get_feature_columns
    calendar = load_strategy_calendar(calendar)
    raw = load_all_stock_data_from_db(sectors=sectors, index_codes=index_codes,
                                      use_tushare=use_tushare, strict_validation=True)
    required = tuple(dict.fromkeys(['open', 'high', 'low', 'close', 'vol', 'amount', 'turnover_rate']
                                  + get_feature_columns(raw)))
    raw, selection = select_strategy_snapshot(raw, calendar, request_time, required_columns=required)
    featured = prepare_panel_for_training(raw, forward_days=forward_days, use_tushare=use_tushare,
                                          n_workers=n_workers, use_parallel=True, keep_unlabelled=True)
    # No full-history fitted feature selector before evaluating earlier dates.
    # Warm-up rows are removed only from training inside dated_samples.
    features = get_feature_columns(featured)
    result = run_walk_forward_backtest(featured, features, top_n=top_n,
                                       train_window=train_window, rebalance_days=rebalance_days,
                                       initial_capital=1000000., calendar=calendar, prices=raw,
                                       forward_days=forward_days)
    result['data_selection'] = selection
    result['feature_selection'] = 'fixed numeric feature schema, no full-history probe'
    return result


def run_probe_selection_only(top_n=10, forward_days=5, use_tushare=False, skip_update=False, delay=0.5, sectors=None, index_codes=None):
    """
    仅执行探针法特征筛选，不训练最终模型。
    """
    from data_processor.panel_builder import (
        incremental_update,
        prepare_panel_for_training,
    )
    from analysis.ranking_predictor import get_feature_columns

    logger.info("=" * 60)
    logger.info("探针法特征筛选")
    logger.info("=" * 60)

    # 1. 增量采集
    panel_df = incremental_update(
        use_tushare=use_tushare, delay=delay,
        sectors=sectors, index_codes=index_codes, start_date=None
    )
    if panel_df.empty:
        logger.error("无有效数据")
        return None

    # 2. 计算特征
    featured_df = prepare_panel_for_training(panel_df, forward_days=forward_days, use_tushare=use_tushare)
    if featured_df.empty:
        logger.error("特征计算失败")
        return None

    # 3. 探针法筛选
    feature_names = get_feature_columns(featured_df)
    train_result = train_ranking_model(
        featured_df,
        feature_names=feature_names,
        use_probe=True,
        save_name="probe_only",
    )
    return train_result


def run_clean(stock_code="000001", use_tushare=False):
    """
    独立数据清洗模式：从数据库加载单只股票数据并执行清洗。

    Args:
        stock_code: 股票代码
        use_tushare: 是否使用 Tushare 数据源格式

    Returns:
        DataFrame: 清洗后的数据
    """
    logger.info("=" * 50)
    logger.info(f"开始数据清洗: {stock_code}")
    logger.info("=" * 50)

    from data_processor.panel_builder import load_all_stock_data_from_db

    panel_df = load_all_stock_data_from_db(
        sectors=None, index_codes=None, use_tushare=use_tushare
    )
    if panel_df.empty:
        logger.error("数据库无数据，请先执行 collect 采集数据")
        return None

    stock_df = panel_df[panel_df['ts_code'] == stock_code].copy()
    if stock_df.empty:
        logger.error(f"未找到股票 {stock_code} 的数据")
        return None

    rows_before = len(stock_df)
    cleaner = DataCleaner()
    df_cleaned = cleaner.clean_stock_data(stock_df, stock_code)
    rows_after = len(df_cleaned) if df_cleaned is not None else 0

    logger.info(f"清洗完成: {rows_before} 行 -> {rows_after} 行 "
                f"(移除 {rows_before - rows_after} 行)")
    return df_cleaned


def run_dashboard():
    """启动 Streamlit 交互式看板"""
    dashboard_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        'visualization', 'dashboard.py'
    )
    if not os.path.exists(dashboard_path):
        logger.error(f"看板文件不存在: {dashboard_path}")
        return

    logger.info(f"启动 Streamlit 看板: {dashboard_path}")
    subprocess.run([sys.executable, '-m', 'streamlit', 'run', dashboard_path])



# ============================================================
#  子命令调度函数 (V0.1 统一入口)
# ============================================================

def cmd_collect(args, sector_list, index_list):
    """collect 子命令：数据采集"""
    init_database()

    if args.full:
        logger.info("=" * 60)
        logger.info("全量更新：数据采集 + 传统特征 + Transformer 特征")
        logger.info(f"数据源: {'Tushare' if args.use_tushare else 'Akshare'}")
        logger.info(f"跳过采集: {args.skip_update}")
        logger.info("=" * 60)

        # ---- 步骤1：采集增量数据 ----
        logger.info("\n" + "=" * 60)
        if args.skip_update:
            logger.info("步骤 1/3：从数据库加载已有数据（跳过采集）")
            logger.info("=" * 60)
            from data_processor.panel_builder import load_all_stock_data_from_db
            panel_df = load_all_stock_data_from_db(
                sectors=sector_list, index_codes=index_list, use_tushare=args.use_tushare
            )
        else:
            logger.info("步骤 1/3：增量数据采集（stock_basic + stock_daily）")
            logger.info("=" * 60)
            panel_df = run_data_collection(
                use_tushare=args.use_tushare, delay=args.delay, start_date=args.start_date
            )
        if panel_df.empty:
            logger.error("无有效数据，全量更新终止")
            return

        # ---- 步骤2：传统管线特征入库 ----
        logger.info("\n" + "=" * 60)
        logger.info("步骤 2/3：传统管线特征（72列，入库 stock_features + parquet缓存）")
        logger.info("=" * 60)
        from data_processor.panel_builder import prepare_panel_for_training

        if not panel_df.empty:
            prepare_panel_for_training(
                panel_df, forward_days=args.forward_days, use_tushare=args.use_tushare,
                n_workers=args.n_workers, use_parallel=True, save_to_db=True,
            )
            logger.info("传统管线特征更新完成")
        else:
            logger.error("无有效数据，传统特征跳过")

        # ---- 步骤3：Transformer 管线特征 ----
        logger.info("\n" + "=" * 60)
        logger.info("步骤 3/3：Transformer 管线特征")
        logger.info("=" * 60)
        from analysis.transformer_trainer import compute_and_save_features as compute_transformer_features

        if not panel_df.empty:
            t_config = build_transformer_config(args, sector_list, index_list)
            save_path, feature_cols, val_start = compute_transformer_features(
                panel_df, config=t_config,
                use_parallel=True, n_workers=args.n_workers,
            )
            if save_path:
                logger.info(f"Transformer 管线特征更新完成: {save_path}")
        else:
            logger.error("无有效数据，Transformer 特征跳过")

        # ---- 完成 ----
        logger.info("\n" + "=" * 60)
        logger.info("全量更新完成！")
        logger.info("=" * 60)
    else:
        # 普通增量采集（原 collect / incremental 模式）
        logger.info("执行数据采集（增量更新）...")
        run_data_collection(use_tushare=args.use_tushare, delay=args.delay, start_date=args.start_date)


def cmd_clean(args):
    """clean 子命令：数据清洗"""
    logger.info(f"执行数据清洗: {args.stock}")
    init_database()
    result = run_clean(stock_code=args.stock, use_tushare=args.use_tushare)
    if result is not None:
        logger.info(f"清洗后数据: {len(result)} 行")
    else:
        logger.error("数据清洗失败")


def cmd_feature(args, sector_list, index_list):
    """feature 子命令：特征计算"""
    init_database()

    if args.pipeline == 'traditional':
        # 原 save_features 模式
        logger.info("执行传统管线特征计算（72列，入库 stock_features）...")
        from data_processor.panel_builder import incremental_update, prepare_panel_for_training

        logger.info("步骤1: 加载面板数据...")
        panel_df = incremental_update(
            use_tushare=args.use_tushare, delay=args.delay,
            sectors=sector_list, index_codes=index_list, start_date=args.start_date
        )
        if panel_df.empty:
            logger.error("无有效数据，请先执行 collect 采集数据")
        else:
            logger.info("步骤2: 计算所有股票特征并入库...")
            prepare_panel_for_training(
                panel_df, forward_days=args.forward_days, use_tushare=args.use_tushare,
                n_workers=args.n_workers, use_parallel=True, save_to_db=True,
            )
            logger.info("传统特征批量入库完成")

    elif args.pipeline == 'transformer':
        # 原 transform_features / incremental_features 模式
        logger.info("执行 Transformer 特征计算（仅计算，不训练）...")
        from analysis.transformer_trainer import compute_and_save_features
        from data_processor.panel_builder import incremental_update, load_all_stock_data_from_db

        if args.skip_update:
            logger.info("步骤1: 从数据库加载已有数据（跳过采集）...")
            panel_df = load_all_stock_data_from_db(
                sectors=sector_list, index_codes=index_list, use_tushare=args.use_tushare
            )
        else:
            logger.info("步骤1: 增量数据采集...")
            panel_df = incremental_update(
                use_tushare=args.use_tushare, delay=args.delay,
                sectors=sector_list, index_codes=index_list, start_date=args.start_date
            )
        if panel_df.empty:
            logger.error("无有效数据，请先执行 collect 采集数据")
            return

        t_config = build_transformer_config(args, sector_list, index_list)
        save_path, feature_cols, val_start = compute_and_save_features(
            panel_df, config=t_config,
            use_parallel=True, n_workers=args.n_workers,
        )
        if save_path:
            logger.info(f"Transformer 特征计算完成: {save_path}")
            logger.info(f"共 {len(feature_cols)} 个特征列，验证集起始: {val_start}")


def cmd_train(args, sector_list, index_list):
    """train 子命令：模型训练"""
    if args.pipeline == 'traditional' and args.model == 'lstm':
        raise ValueError('LSTM training is not implemented')
    if args.pipeline == 'traditional' and args.backtest:
        if args.model != 'xgboost':
            raise ValueError('Validated backtest requires binary xgboost; requested model is unsupported')
        load_strategy_calendar(args.calendar)

    if args.pipeline == 'traditional':
        # 原 demo 模式的训练部分：单股采集 → 处理 → 分析 → 训练
        logger.info("运行传统管线训练（单股 demo 流程）...")
        init_database()

        collector = DataCollector(use_tushare=args.use_tushare)
        end_date = datetime.now().strftime('%Y%m%d')
        df = collector.fetch_single(args.stock, '20240101', end_date)

        if df is None or df.empty:
            logger.error("未能获取数据")
            return

        df_raw = df.copy()
        df_processed = run_data_processing(df, args.stock)
        run_analysis(df_processed, args.stock)
        if args.backtest:
            return run_backtest(df_processed, args.stock, calendar=args.calendar, model_type=args.model)
        prediction_result = run_prediction(df_processed, args.model)
        save_data_to_db(args.stock, df_raw, df_processed, prediction_result, args.model)

        logger.info("传统管线训练完成")

    elif args.pipeline == 'ranking':
        # 原 rank / rank_backtest 模式
        if args.backtest:
            logger.info("运行排名策略回测...")
            return run_ranking_backtest_pipeline(
                top_n=args.top_n,
                use_probe=not args.no_probe,
                forward_days=args.forward_days,
                train_window=args.train_window,
                rebalance_days=args.rebalance_days,
                use_tushare=args.use_tushare,
                delay=args.delay,
                skip_update=args.skip_update,
                use_gpu=args.gpu,
                n_workers=args.n_workers,
                sectors=sector_list,
                index_codes=index_list,
                calendar=args.calendar,
            )
        else:
            logger.info("运行排名管线训练...")
            return run_ranking_pipeline(
                top_n=args.top_n,
                use_probe=not args.no_probe,
                forward_days=args.forward_days,
                use_tushare=args.use_tushare,
                delay=args.delay,
                skip_update=args.skip_update,
                use_gpu=args.gpu,
                n_workers=args.n_workers,
                sectors=sector_list,
                index_codes=index_list,
                calendar=args.calendar,
            )
        logger.info("排名管线训练完成")

    elif args.pipeline == 'transformer':
        # 原 transform_train 模式
        logger.info("运行 Transformer 训练...")
        from analysis.transformer_trainer import run_transformer_training
        from data_processor.panel_builder import incremental_update, load_all_stock_data_from_db

        t_config = build_transformer_config(args, sector_list, index_list)
        if args.feature_path:
            t_config['output_dir'] = os.path.dirname(os.path.abspath(args.feature_path))

        if args.feature_path:
            logger.info(f"使用预计算特征: {args.feature_path}")
            result = run_transformer_training(
                feature_path=args.feature_path,
                config=t_config,
                use_multi_head=t_config['use_multi_head'],
                num_epochs=t_config.get('num_epochs'),
                learning_rate=t_config.get('learning_rate'),
            )
        else:
            if args.skip_update:
                logger.info("步骤1: 从数据库加载已有数据（跳过采集）...")
                panel_df = load_all_stock_data_from_db(
                    sectors=sector_list, index_codes=index_list, use_tushare=args.use_tushare
                )
            else:
                logger.info("步骤1: 增量数据采集...")
                panel_df = incremental_update(
                    use_tushare=args.use_tushare,
                    sectors=sector_list, index_codes=index_list,
                    start_date=args.start_date,
                )
            if panel_df.empty:
                logger.error("无有效数据，请先执行 collect 采集数据")
                return

            result = run_transformer_training(
                panel_df=panel_df,
                config=t_config,
                use_multi_head=t_config['use_multi_head'],
                num_epochs=t_config.get('num_epochs'),
                learning_rate=t_config.get('learning_rate'),
            )
        if result:
            logger.info(f"Transformer 训练完成，模型保存至: {result['model_path']}")


def cmd_predict(args, sector_list, index_list):
    """predict 子命令：模型预测"""
    if args.pipeline == 'traditional' and args.model == 'lstm':
        raise ValueError('LSTM training is not implemented')
    if args.pipeline == 'traditional' and args.backtest:
        if args.model != 'xgboost':
            raise ValueError('Validated backtest requires binary xgboost; requested model is unsupported')
        load_strategy_calendar(args.calendar)

    if args.pipeline == 'traditional':
        init_database()

        if args.stock:
            # 单股预测（原 demo / predict / backtest 模式）
            logger.info(f"运行单股预测（{args.stock}, {args.model}）...")
            collector = DataCollector(use_tushare=args.use_tushare)
            end_date = datetime.now().strftime('%Y%m%d')
            df = collector.fetch_single(args.stock, '20240101', end_date)

            if df is None or df.empty:
                logger.error("未能获取数据")
                return

            df_raw = df.copy()
            df_processed = run_data_processing(df, args.stock)
            run_analysis(df_processed, args.stock)
            if args.backtest:
                return run_backtest(df_processed, args.stock, calendar=args.calendar, model_type=args.model)
            prediction_result = run_prediction(df_processed, args.model)
            save_data_to_db(args.stock, df_raw, df_processed, prediction_result, args.model)

            logger.info("单股预测完成")
        else:
            # 全市场批量预测排名（原 batch_predict_rank 模式）
            logger.info("运行传统管线全市场批量预测排名...")
            from data_processor.panel_builder import (
                load_stock_features_from_db, compute_and_save_traditional_features_batch
            )
            filtered_codes = None
            if sector_list or index_list:
                from data_processor.stock_filter import resolve_stock_codes
                filtered_codes = resolve_stock_codes(
                    sectors=sector_list, index_codes=index_list, use_tushare=args.use_tushare
                )
                if filtered_codes is not None and len(filtered_codes) == 0:
                    logger.error("板块/指数筛选后无股票，停止批量预测")
                    return

            features_df = load_stock_features_from_db(stock_codes=filtered_codes)

            if features_df.empty:
                logger.info("stock_features 缓存为空，先批量计算特征...")
                features_df = compute_and_save_traditional_features_batch(
                    stock_codes=filtered_codes,
                    use_tushare=args.use_tushare, n_workers=args.n_workers
                )
                if features_df.empty:
                    logger.error("特征计算失败，无数据可用")
                    return

            logger.info(f"从 stock_features 加载 {len(features_df)} 行特征, "
                         f"{features_df['ts_code'].nunique()} 只股票")

            features_df['trade_date'] = pd.to_datetime(features_df['trade_date'])
            latest = features_df.loc[features_df.groupby('ts_code')['trade_date'].idxmax()]

            predictor = StockPredictor(model_type=args.model)
            target_col = 'future_direction_1d' if args.model == 'xgboost' else 'future_return_1d'

            feature_names = predictor.prepare_features(features_df, target_column=target_col)
            if feature_names[0] is None:
                logger.error("特征准备失败")
                return
            X_all, y_all, available_features = feature_names

            logger.info(f"使用全市场数据训练{args.model}模型...")
            if args.model == 'xgboost':
                train_result = predictor.train_xgboost(X_all, y_all)
            elif args.model == 'xgboost_regression':
                train_result = predictor.train_xgboost_regression(X_all, y_all)
            elif args.model == 'xgboost_multiclass':
                train_result = predictor.train_xgboost_multiclass(X_all, y_all)
            elif args.model == 'lstm':
                train_result = predictor.train_lstm(X_all, y_all, sequence_length=60)
            else:
                logger.error(f"不支持的模型类型: {args.model}")
                return

            if train_result is None:
                logger.error("模型训练失败")
                return

            metrics = train_result.get('metrics', {})
            logger.info(f"模型评估指标: {metrics}")

            logger.info(f"\n{'='*60}")
            logger.info(f"全市场预测排名 ({args.model})")
            logger.info(f"{'='*60}")

            X_latest = latest[available_features].dropna()
            if X_latest.empty:
                logger.error("最新数据无有效特征")
                return

            if args.model in ('xgboost_regression',):
                scores = predictor.predict_regression(X_latest.values)
                rank_metric = '预测收益率'
            elif args.model in ('xgboost_multiclass',):
                scores = predictor.predict_proba(X_latest.values)
                rank_metric = '期望收益率'
            else:
                scores = predictor.predict_proba(X_latest.values)
                rank_metric = '看涨概率'

            if scores is None:
                logger.error("预测失败")
                return

            ranking_df = pd.DataFrame({
                '股票代码': latest.loc[X_latest.index, 'ts_code'].values,
                '股票名称': latest.loc[X_latest.index, 'name'].values
                    if 'name' in latest.columns else None,
                '预测分数': scores,
            })
            ranking_df = ranking_df.sort_values('预测分数', ascending=False).reset_index(drop=True)
            ranking_df.index += 1

            print(f"\n{'='*60}")
            print(f"全市场{args.model}预测排名 (按{rank_metric}降序)")
            print(f"{'='*60}")
            print(ranking_df.head(args.top_n).to_string(index=True))
            print(f"{'='*60}")
            print(f"共 {len(ranking_df)} 只股票参与排名")

            output_path = os.path.join(os.path.dirname(__file__), 'output', f'ranking_{args.model}.csv')
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            ranking_df.to_csv(output_path, index=True, index_label='排名', encoding='utf-8-sig')
            logger.info(f"排名结果已保存至: {output_path}")

            predictor.save_model(f'batch_{args.model}_latest')

    elif args.pipeline == 'ranking':
        # 原 rank / rank_backtest 的预测部分
        if args.backtest:
            logger.info("运行排名策略回测...")
            return run_ranking_backtest_pipeline(
                top_n=args.top_n,
                use_probe=not args.no_probe,
                forward_days=args.forward_days,
                train_window=args.train_window,
                rebalance_days=args.rebalance_days,
                use_tushare=args.use_tushare,
                delay=args.delay,
                skip_update=args.skip_update,
                use_gpu=args.gpu,
                n_workers=args.n_workers,
                sectors=sector_list,
                index_codes=index_list,
                calendar=args.calendar,
            )
        else:
            logger.info("运行排名管线预测...")
            return run_ranking_pipeline(
                top_n=args.top_n,
                use_probe=not args.no_probe,
                forward_days=args.forward_days,
                use_tushare=args.use_tushare,
                delay=args.delay,
                skip_update=args.skip_update,
                use_gpu=args.gpu,
                n_workers=args.n_workers,
                sectors=sector_list,
                index_codes=index_list,
                calendar=args.calendar,
            )

    elif args.pipeline == 'transformer':
        # 原 transform_predict 模式
        logger.info("运行 Transformer 预测...")
        from analysis.transformer_trainer import predict_top_stocks_transformer
        from data_processor.panel_builder import incremental_update

        t_config = build_transformer_config(args, sector_list, index_list)
        if args.feature_path:
            t_config['output_dir'] = os.path.dirname(os.path.abspath(args.feature_path))

        if args.feature_path:
            logger.info(f"使用预计算特征: {args.feature_path}")
            result_df = predict_top_stocks_transformer(
                feature_path=args.feature_path,
                config=t_config,
                top_k=args.top_n,
            )
        else:
            logger.info("步骤1: 增量数据采集...")
            panel_df = incremental_update(
                use_tushare=args.use_tushare,
                sectors=sector_list, index_codes=index_list,
                start_date=args.start_date,
            )
            if panel_df.empty:
                logger.error("无有效数据")
                return

            result_df = predict_top_stocks_transformer(
                panel_df=panel_df,
                config=t_config,
                top_k=args.top_n,
            )
        if result_df is not None:
            output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'output')
            os.makedirs(output_dir, exist_ok=True)
            output_path = os.path.join(output_dir, 'result.csv')
            scores = result_df['预测分数'].values
            temperature = t_config.get('weight_temperature', 0.5)
            scores_norm = scores / temperature
            exp_scores = np.exp(scores_norm - np.max(scores_norm))
            weights = exp_scores / exp_scores.sum()
            weights[:-1] = np.floor(weights[:-1] * 100) / 100
            weights[-1] = 1.0 - weights[:-1].sum()
            weights = np.array([round(float(w), 2) for w in weights])

            output_df = pd.DataFrame({
                'stock_id': result_df['股票代码'].values[:5],
                'weight': weights,
            })
            output_df.to_csv(output_path, index=False)
            logger.info(f"预测结果已保存至: {output_path}")
            print(result_df.to_string(index=False))


def cmd_dashboard(args):
    """dashboard 子命令：启动 Streamlit 看板"""
    run_dashboard()


def main():
    """主函数 - 统一 CLI 入口（6 子命令）"""

    # ---- 共享参数（parent parser） ----
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--use_tushare', action='store_true',
                        help='兼容旧参数（已弃用，分析端统一读取 MySQL）')
    common.add_argument('--delay', type=float, default=0.5,
                        help='数据采集请求间隔（秒）')
    common.add_argument('--start_date', type=str, default=None,
                        help='数据采集起始日期，格式 YYYYMMDD')
    common.add_argument('--skip_update', action='store_true',
                        help='跳过数据采集，直接从数据库读取已有数据')
    common.add_argument('--gpu', action='store_true',
                        help='使用 GPU (CUDA) 加速')
    common.add_argument('--n_workers', type=int, default=None,
                        help='并行计算进程数（默认 CPU 核心数）')
    common.add_argument('--sectors', type=str, default=None,
                        help='行业板块筛选，逗号分隔，如 "银行,医药"')
    common.add_argument('--index', type=str, default=None,
                        help='指数成分股筛选，逗号分隔指数代码，如 "000300"')
    common.add_argument('--calendar', type=str, default=None,
                        help='策略验证必需：可信交易日历 JSON 本地路径（不猜工作日）')
    common.add_argument('--report_dir', type=str, default='reports/strategy_validation',
                        help='本地策略结果与日期证据目录（每次新建，不覆盖旧报告）')

    # ---- 主解析器 ----
    parser = argparse.ArgumentParser(
        description='股票分析系统 - 统一入口',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令说明:
  collect    数据采集（增量 / 全量）
  clean      数据清洗
  feature    特征计算（传统 / Transformer）
  train      模型训练
  predict    模型预测
  dashboard  启动 Streamlit 看板

示例:
  python main.py collect
  python main.py collect --full
  python main.py clean --stock 000001
  python main.py feature --pipeline traditional
  python main.py feature --pipeline transformer --skip_update
  python main.py train --pipeline ranking --skip_update
  python main.py train --pipeline transformer --feature_path path/to/features.parquet
  python main.py predict --pipeline traditional --stock 000001
  python main.py predict --pipeline ranking --backtest
  python main.py predict --pipeline transformer
  python main.py dashboard
        """
    )
    subparsers = parser.add_subparsers(dest='command', help='运行模式')

    # ---- collect ----
    p_collect = subparsers.add_parser('collect', parents=[common],
                                       help='数据采集')
    p_collect.add_argument('--full', action='store_true',
                           help='全量更新（采集 + 传统特征 + Transformer 特征）')

    # ---- clean ----
    p_clean = subparsers.add_parser('clean', parents=[common],
                                     help='数据清洗')
    p_clean.add_argument('--stock', type=str, default='000001',
                         help='股票代码')

    # ---- feature ----
    p_feature = subparsers.add_parser('feature', parents=[common],
                                       help='特征计算')
    p_feature.add_argument('--pipeline', type=str, default='traditional',
                           choices=['traditional', 'transformer'],
                           help='特征管线类型')
    p_feature.add_argument('--forward_days', type=int, default=5,
                           help='远期收益天数（标签计算）')

    # ---- train ----
    p_train = subparsers.add_parser('train', parents=[common],
                                     help='模型训练')
    p_train.add_argument('--pipeline', type=str, default='traditional',
                         choices=['traditional', 'ranking', 'transformer'],
                         help='训练管线')
    p_train.add_argument('--stock', type=str, default='000001',
                         help='股票代码（传统管线）')
    p_train.add_argument('--model', type=str, default='xgboost',
                         choices=['xgboost', 'xgboost_regression',
                                  'xgboost_multiclass', 'lstm'],
                         help='预测模型类型')
    p_train.add_argument('--backtest', action='store_true',
                         help='训练后进行回测')
    p_train.add_argument('--no_probe', action='store_true',
                         help='不使用探针法特征筛选')
    p_train.add_argument('--forward_days', type=int, default=5,
                         help='远期收益天数')
    p_train.add_argument('--top_n', type=int, default=10,
                         help='排名选股数量')
    p_train.add_argument('--train_window', type=int, default=365,
                         help='滚动训练窗口（天）')
    p_train.add_argument('--rebalance_days', type=int, default=5,
                         help='调仓周期（交易日）')
    p_train.add_argument('--feature_path', type=str, default=None,
                         help='预计算特征文件路径')
    p_train.add_argument('--use_sector_filter', action='store_true',
                         help='使用板块强度筛选')
    p_train.add_argument('--save_features_db', action='store_true',
                         help='将特征数据保存到 stock_features 表')
    p_train.add_argument('--transformer_model', type=str, default='multi_head',
                         choices=['single_head', 'multi_head'],
                         help='Transformer模型类型')
    p_train.add_argument('--transformer_epochs', type=int, default=None,
                         help='Transformer训练轮数')
    p_train.add_argument('--transformer_lr', type=float, default=None,
                         help='Transformer学习率')
    p_train.add_argument('--transformer_features', type=str, default='158+39',
                         choices=['39', '158+39'],
                         help='Transformer特征集')

    # ---- predict ----
    p_predict = subparsers.add_parser('predict', parents=[common],
                                       help='模型预测')
    p_predict.add_argument('--pipeline', type=str, default='traditional',
                           choices=['traditional', 'ranking', 'transformer'],
                           help='预测管线')
    p_predict.add_argument('--stock', type=str, default=None,
                           help='股票代码（传统管线单股预测；不指定则全市场批量排名）')
    p_predict.add_argument('--model', type=str, default='xgboost',
                           choices=['xgboost', 'xgboost_regression',
                                    'xgboost_multiclass', 'lstm'],
                           help='预测模型类型')
    p_predict.add_argument('--top_n', type=int, default=10,
                           help='排名选股数量')
    p_predict.add_argument('--backtest', action='store_true',
                           help='预测后进行回测')
    p_predict.add_argument('--no_probe', action='store_true',
                           help='不使用探针法特征筛选')
    p_predict.add_argument('--forward_days', type=int, default=5,
                           help='远期收益天数')
    p_predict.add_argument('--train_window', type=int, default=365,
                           help='滚动训练窗口（天）')
    p_predict.add_argument('--rebalance_days', type=int, default=5,
                           help='调仓周期（交易日）')
    p_predict.add_argument('--feature_path', type=str, default=None,
                           help='预计算特征文件路径')
    p_predict.add_argument('--use_sector_filter', action='store_true',
                           help='使用板块强度筛选')
    p_predict.add_argument('--transformer_model', type=str, default='multi_head',
                           choices=['single_head', 'multi_head'],
                           help='Transformer模型类型')
    p_predict.add_argument('--transformer_epochs', type=int, default=None,
                           help='Transformer训练轮数')
    p_predict.add_argument('--transformer_lr', type=float, default=None,
                           help='Transformer学习率')
    p_predict.add_argument('--transformer_features', type=str, default='158+39',
                           choices=['39', '158+39'],
                           help='Transformer特征集')

    # ---- dashboard ----
    subparsers.add_parser('dashboard', help='启动 Streamlit 看板')

    # ---- 解析 & 分发 ----
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    sector_list = [s.strip() for s in args.sectors.split(',')] if args.sectors else None
    index_list = [i.strip() for i in args.index.split(',')] if args.index else None

    logger.info(f"启动股票分析系统，命令: {args.command}")

    dispatch = {
        'collect':   lambda: cmd_collect(args, sector_list, index_list),
        'clean':     lambda: cmd_clean(args),
        'feature':   lambda: cmd_feature(args, sector_list, index_list),
        'train':     lambda: cmd_train(args, sector_list, index_list),
        'predict':   lambda: cmd_predict(args, sector_list, index_list),
        'dashboard': lambda: cmd_dashboard(args),
    }
    result = dispatch[args.command]()
    if result is not None and args.command in ('train', 'predict'):
        from analysis.strategy_reports import export_strategy_result
        export_strategy_result(result, args.report_dir, args.pipeline)
    return result


if __name__ == '__main__':
    main()
