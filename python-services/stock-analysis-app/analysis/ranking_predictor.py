# -*- coding: utf-8 -*-
"""
全市场排名预测器 (Ranking Predictor)

基于探针法筛选的特征，训练 LightGBM 回归模型预测股票未来收益率，
输出全市场排名最高的 Top N 股票。
"""

import os
import pickle
import json
import numpy as np
import pandas as pd
import lightgbm as lgb
from datetime import datetime
from config.logging_config import get_logger
logger = get_logger(__name__)
from config.settings import RANKING_CONFIG
from data_processor.probe_selection import probe_feature_selection, load_selected_features

# 模型保存目录
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


def get_feature_columns(df):
    """
    获取可用于训练的数值特征列名。

    排除: 非数值列、标签列、标识列
    """
    exclude_cols = {
        'label', 'future_return_1d', 'future_return_5d',
        'future_direction_1d', 'future_direction_5d',
        'ts_code', 'trade_date', '日期', '股票名称',
        # log_return 是合法历史特征（= log(close/close.shift(1))），不排除
        'name', 'symbol'
    }

    feature_cols = []
    for col in df.select_dtypes(include=[np.number]).columns:
        if col.lower() not in exclude_cols and col not in exclude_cols:
            feature_cols.append(col)

    return feature_cols


def _check_gpu_available():
    """检查是否有可用的 GPU（CUDA）。"""
    try:
        import lightgbm as lgb
        # 尝试创建 GPU 设备来检测
        lgb_params_test = {
            'objective': 'regression',
            'device': 'gpu',
            'gpu_device_id': 0,
        }
        return True
    except Exception:
        return False


def train_ranking_model(panel_df, feature_names=None, use_probe=True,
                        probe_config=None, save_name=None, use_gpu=False):
    """
    训练排名预测模型。

    流程:
    1. (可选) 探针法特征筛选
    2. 按时间划分训练/验证集
    3. 训练 LightGBM 回归模型（可选 GPU 加速）
    4. 保存模型和特征列表

    参数:
        panel_df: 面板数据（含特征和 label 列）
        feature_names: 候选特征名列表，None 则自动获取
        use_probe: 是否使用探针法筛选特征
        probe_config: 探针法参数配置
        save_name: 模型保存文件名（不含扩展名）
        use_gpu: 是否使用 GPU (CUDA) 加速训练
    返回:
        dict: 包含模型、筛选后特征、评估指标
    """
    if panel_df.empty:
        logger.error("面板数据为空")
        return None

    # 自动获取特征
    if feature_names is None:
        feature_names = get_feature_columns(panel_df)
        logger.info(f"自动获取 {len(feature_names)} 个候选特征")

    if not feature_names:
        logger.error("无可用特征")
        return None

    # 确保所有特征存在
    available_features = [f for f in feature_names if f in panel_df.columns]
    missing = [f for f in feature_names if f not in panel_df.columns]
    if missing:
        logger.warning(f"以下特征不存在，已跳过: {missing[:5]}...")
    feature_names = available_features

    # [防泄漏] 先按时间划分训练/测试集，再在训练集上做探针法筛选
    panel_df = panel_df.sort_values('trade_date').reset_index(drop=True)
    split_point = int(len(panel_df) * 0.8)
    train_df = panel_df.iloc[:split_point].copy()
    test_df = panel_df.iloc[split_point:].copy()

    # 探针法筛选（仅在训练集上运行，防止测试集信息泄漏到特征选择中）
    if use_probe:
        logger.info("开始探针法特征筛选（仅训练集）...")
        probe_cfg = probe_config or {
            'n_iter': RANKING_CONFIG['probe_n_iter'],
            'n_noise': RANKING_CONFIG['probe_n_noise'],
            'train_ratio': 0.9,
            'seed': 42,
        }

        selected_features, log_records = probe_feature_selection(
            train_df, feature_names,
            output_path=os.path.join(MODEL_DIR, 'probe_selection_result.json'),
            **probe_cfg
        )

        if not selected_features:
            logger.error("探针法筛选后无特征保留")
            return None
    else:
        selected_features = feature_names
        log_records = []

    logger.info(f"使用 {len(selected_features)} 个特征训练排名模型")

    X_train = train_df[selected_features].values.astype('float32')
    y_train = train_df['label'].values.astype('float32')
    X_test = test_df[selected_features].values.astype('float32')
    y_test = test_df['label'].values.astype('float32')

    # 清理 NaN/Inf
    train_mask = np.isfinite(X_train).all(axis=1) & np.isfinite(y_train)
    test_mask = np.isfinite(X_test).all(axis=1) & np.isfinite(y_test)
    X_train, y_train = X_train[train_mask], y_train[train_mask]
    X_test, y_test = X_test[test_mask], y_test[test_mask]

    # 训练 LightGBM
    lgb_params = RANKING_CONFIG['lgb_params'].copy()
    lgb_params.setdefault('seed', 42)  # 固定种子，确保可复现

    # 启用 GPU 加速
    if use_gpu:
        gpu_ok = _check_gpu_available()
        if gpu_ok:
            lgb_params['device'] = 'gpu'
            lgb_params['gpu_device_id'] = 0
            lgb_params['gpu_use_dp'] = True  # 双精度提升训练精度
            logger.info("LightGBM GPU 加速已启用")
        else:
            logger.warning("GPU 不可用，回退到 CPU 训练")

    train_data = lgb.Dataset(X_train, label=y_train)
    test_data = lgb.Dataset(X_test, label=y_test, reference=train_data)

    logger.info(f"训练 LightGBM: train={len(X_train)}, test={len(X_test)}, "
                f"device={'GPU' if lgb_params.get('device') == 'gpu' else 'CPU'}")

    model = lgb.train(
        lgb_params,
        train_data,
        num_boost_round=lgb_params.get('n_estimators', 500),
        valid_sets=[test_data],
        callbacks=[
            lgb.early_stopping(20, verbose=False),
            lgb.log_evaluation(period=50),
        ]
    )

    # 测试集评估
    y_pred = model.predict(X_test)
    rmse = np.sqrt(np.mean((y_test - y_pred) ** 2))
    direction_acc = (np.sign(y_test) == np.sign(y_pred)).mean()

    logger.info(f"排名模型评估: RMSE={rmse:.6f}, 方向准确率={direction_acc:.2%}")

    # 保存模型
    if save_name is None:
        save_name = f"ranking_model_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    from analysis.model_registry import build_metadata, save_model as reg_save

    metadata = build_metadata(
        model_name=save_name,
        model_type='ranking_lgb',
        feature_names=selected_features,
        train_start_date=train_df['trade_date'].min() if 'trade_date' in train_df.columns else None,
        train_end_date=train_df['trade_date'].max() if 'trade_date' in train_df.columns else None,
        metrics={'RMSE': float(rmse), 'Direction_Accuracy': float(direction_acc)},
        params=lgb_params,
        seed=42,
        extra={'probe_log': log_records},
    )

    model_bundle = {
        'model': model,
        'feature_names': selected_features,
        'model_type': 'ranking_lgb',
        'metrics': metadata['metrics'],
    }

    model_path = reg_save(model_bundle, metadata, save_name)

    logger.info(f"模型已保存至: {model_path}")

    return {
        'model': model,
        'feature_names': selected_features,
        'metrics': metadata['metrics'],
        'log_records': log_records,
        'model_path': model_path,
    }


def load_ranking_model(model_path=None):
    """
    加载排名模型。

    参数:
        model_path: 模型路径（.pkl），None 则自动加载最新模型
    返回:
        tuple: (model, feature_names, metrics)
    """
    from analysis.model_registry import load_model as reg_load

    model_bundle, metadata = reg_load(model_path, model_type='ranking_model')
    if model_bundle is None:
        return None, None, None

    model = model_bundle.get('model')
    feature_names = model_bundle.get('feature_names', [])
    metrics = model_bundle.get('metrics', {})

    if metadata:
        logger.info("模型训练时间: %s ~ %s",
                     metadata.get('train_start_date'), metadata.get('train_end_date'))

    return model, feature_names, metrics


def predict_top_n(panel_df, model_path=None, top_n=None):
    """
    对最新一天的股票预测收益率并排名，返回 Top N。

    参数:
        panel_df: 面板数据
        model_path: 模型路径
        top_n: 选股数量
    返回:
        DataFrame: 排名结果（含 ts_code, 股票名称, 预测收益率, 排名）
    """
    if top_n is None:
        top_n = RANKING_CONFIG['top_n']

    model, feature_names, metrics = load_ranking_model(model_path)
    if model is None:
        return None

    # 取最新一天的数据
    latest_date = panel_df['trade_date'].max()
    latest_df = panel_df[panel_df['trade_date'] == latest_date].copy()

    if latest_df.empty:
        logger.error("无最新交易日数据")
        return None

    # 确保特征存在
    available = [f for f in feature_names if f in latest_df.columns]
    missing = [f for f in feature_names if f not in latest_df.columns]
    if missing:
        logger.warning(f"最新数据缺少 {len(missing)} 个特征，已填充0")
        for col in missing:
            latest_df[col] = 0

    X_latest = latest_df[feature_names].values.astype('float32')
    X_latest = np.nan_to_num(X_latest, nan=0.0, posinf=0.0, neginf=0.0)

    # 预测
    pred_returns = model.predict(X_latest)
    latest_df['预测收益率'] = pred_returns
    latest_df['排名'] = latest_df['预测收益率'].rank(ascending=False, method='min').astype(int)

    # 排序
    latest_df = latest_df.sort_values('排名').reset_index(drop=True)

    # 取 Top N
    display_cols = ['ts_code', 'trade_date', '预测收益率', '排名']
    if '股票名称' in latest_df.columns:
        display_cols.insert(1, '股票名称')
    top_stocks = latest_df[display_cols].head(top_n)

    logger.info(f"\n{'='*60}")
    logger.info(f"全市场排名 Top {top_n} ({latest_date.strftime('%Y-%m-%d')})")
    logger.info(f"{'='*60}")
    for _, row in top_stocks.iterrows():
        name = row.get('股票名称', '')
        logger.info(f"  #{int(row['排名']):2d}  {row['ts_code']}  {name: <8s}  "
                    f"预测收益率: {row['预测收益率']:>7.2%}")
    logger.info(f"{'='*60}")

    return top_stocks


def predict_full_ranking(panel_df, model_path=None):
    """
    对最新一天的所有股票预测收益率并返回完整排名。

    参数:
        panel_df: 面板数据
        model_path: 模型路径
    返回:
        DataFrame: 完整排名结果
    """
    model, feature_names, _ = load_ranking_model(model_path)
    if model is None:
        return None

    # 取最新一天的数据
    latest_date = panel_df['trade_date'].max()
    latest_df = panel_df[panel_df['trade_date'] == latest_date].copy()

    # 确保特征存在
    for col in feature_names:
        if col not in latest_df.columns:
            latest_df[col] = 0

    X_latest = latest_df[feature_names].values.astype('float32')
    X_latest = np.nan_to_num(X_latest, nan=0.0, posinf=0.0, neginf=0.0)

    pred_returns = model.predict(X_latest)
    latest_df['预测收益率'] = pred_returns
    latest_df['排名'] = latest_df['预测收益率'].rank(ascending=False, method='min').astype(int)

    latest_df = latest_df.sort_values('排名').reset_index(drop=True)
    display_cols = ['ts_code', 'trade_date', '预测收益率', '排名', 'close']
    if '股票名称' in latest_df.columns:
        display_cols.insert(1, '股票名称')
    return latest_df[display_cols]
