# -*- coding: utf-8 -*-
"""
探针法特征筛选模块 (Probe/Shadow Feature Selection)

核心思想：注入随机噪声特征作为"间谍"，只有重要性超过"运气最好的间谍"的真实特征才保留。
三个任务（分类/回归/方向）同台竞技，必须全部过关才能留下。

直接在 _preprocess_common 输出的 DataFrame 上工作（每行=一只股票一天，列=158+39特征+label）。
"""

from config.logging_config import get_logger
logger = get_logger(__name__)

import numpy as np
import pandas as pd
import lightgbm as lgb
import json
import os


def generate_probe_labels(df, date_column='trade_date'):
    """
    从已有的 label 列生成三个任务标签。

    参数:
        df: 包含 'label' 列的 DataFrame（5日远期收益率）
        date_column: 日期列名，支持 'trade_date' 或 '日期'
    返回:
        label_cls: 当日是否超过当日中位数（按日期分组计算）
        label_reg: 原始收益率（直接用 label）
        label_dir: label 是否为正
    """
    # 兼容 '日期' 和 'trade_date' 列名
    if '日期' in df.columns:
        date_column = '日期'
    elif 'trade_date' in df.columns:
        date_column = 'trade_date'

    # label_cls: 按日期分组，超过当日中位数为1
    daily_median = df.groupby(date_column)['label'].transform('median')
    label_cls = (df['label'] > daily_median).astype(int)

    # label_reg: 原始收益率
    label_reg = df['label'].values.astype('float32')

    # label_dir: 是否为正
    label_dir = (df['label'] > 0).astype(int)

    return label_cls, label_reg, label_dir


def probe_feature_selection(df, feature_names, n_iter=10, n_noise=10,
                            train_ratio=0.9, seed=42, output_path=None):
    """
    增强探针法因子筛选（三任务 + 多噪音 + 最大值基准）。

    关键改进：
    - 使用 split（分裂次数）而非 gain 作为重要性指标，更稳定
    - 增加 boosting 轮数，确保特征重要性充分展开
    - 三个任务独立验证，必须全部过关

    参数:
        df: 包含特征和 'label' 列的 DataFrame
        feature_names: 候选特征名列表
        n_iter: 迭代轮数
        n_noise: 每轮注入的噪声特征数
        train_ratio: 训练集比例（按时间顺序划分）
        seed: 随机种子
        output_path: 保存筛选后特征的 JSON 路径

    返回:
        selected_features: 保留的特征名列表
        log_records: 每轮筛选日志
    """
    np.random.seed(seed)

    # 生成三个任务标签
    label_cls, label_reg, label_dir = generate_probe_labels(df)

    # 按时间顺序划分训练/验证集
    split_point = int(len(df) * train_ratio)
    train_idx = df.index[:split_point]
    val_idx = df.index[split_point:]

    y_train_cls = label_cls[:split_point].astype('int64')
    y_train_reg = label_reg[:split_point].astype('float32')
    y_train_dir = label_dir[:split_point].astype('int64')

    y_val_cls = label_cls[split_point:].astype('int64')
    y_val_reg = label_reg[split_point:].astype('float32')
    y_val_dir = label_dir[split_point:].astype('int64')

    # LightGBM 参数
    params_cls = {'objective': 'binary', 'metric': 'auc', 'seed': seed,
                  'num_leaves': 31, 'learning_rate': 0.05}
    params_reg = {'objective': 'regression', 'metric': 'rmse', 'seed': seed,
                  'num_leaves': 31, 'learning_rate': 0.05}
    params_dir = {'objective': 'binary', 'metric': 'auc', 'seed': seed,
                  'num_leaves': 31, 'learning_rate': 0.05}

    cbs = [lgb.early_stopping(15, verbose=False), lgb.log_evaluation(period=0)]

    logger.info("=" * 60)
    logger.info("探针法特征筛选 (Probe/Shadow Feature Selection)")
    logger.info(f"初始特征数: {len(feature_names)}, 迭代轮数: {n_iter}, 噪声数: {n_noise}")
    logger.info("=" * 60)

    current_features = feature_names.copy()
    log_records = []

    for iter_idx in range(1, n_iter + 1):
        # 提取当前特征矩阵
        X_train_curr = df.loc[train_idx, current_features].values.astype('float32')
        X_val_curr = df.loc[val_idx, current_features].values.astype('float32')

        # 生成噪声特征（高斯白噪声）
        noise_train = np.random.randn(X_train_curr.shape[0], n_noise).astype('float32')
        noise_val = np.random.randn(X_val_curr.shape[0], n_noise).astype('float32')

        # 拼接噪声
        X_train_aug = np.hstack([X_train_curr, noise_train])
        X_val_aug = np.hstack([X_val_curr, noise_val])

        # 构建 LightGBM Dataset
        lgb_train_cls = lgb.Dataset(X_train_aug, y_train_cls)
        lgb_val_cls = lgb.Dataset(X_val_aug, y_val_cls, reference=lgb_train_cls)
        lgb_train_reg = lgb.Dataset(X_train_aug, y_train_reg)
        lgb_val_reg = lgb.Dataset(X_val_aug, y_val_reg, reference=lgb_train_reg)
        lgb_train_dir = lgb.Dataset(X_train_aug, y_train_dir)
        lgb_val_dir = lgb.Dataset(X_val_aug, y_val_dir, reference=lgb_train_dir)

        # 训练三个模型（多轮数确保特征重要性充分展开）
        model_cls = lgb.train(params_cls, lgb_train_cls, valid_sets=[lgb_val_cls],
                              num_boost_round=200, callbacks=cbs)
        model_reg = lgb.train(params_reg, lgb_train_reg, valid_sets=[lgb_val_reg],
                              num_boost_round=200, callbacks=cbs)
        model_dir = lgb.train(params_dir, lgb_train_dir, valid_sets=[lgb_val_dir],
                              num_boost_round=200, callbacks=cbs)

        # 获取特征重要性（split 类型：被分裂次数，比 gain 更稳定）
        imp_cls = model_cls.feature_importance(importance_type='split')
        imp_reg = model_reg.feature_importance(importance_type='split')
        imp_dir = model_dir.feature_importance(importance_type='split')

        # 取噪声特征重要性的最大值作为阈值
        noise_cls = int(np.max(imp_cls[-n_noise:]))
        noise_reg = int(np.max(imp_reg[-n_noise:]))
        noise_dir = int(np.max(imp_dir[-n_noise:]))

        # 剔除条件：三个任务的分裂次数都 <= 对应噪声最大值
        to_remove = []
        for idx, feat in enumerate(current_features):
            if (imp_cls[idx] <= noise_cls and
                    imp_reg[idx] <= noise_reg and
                    imp_dir[idx] <= noise_dir):
                to_remove.append(feat)

        current_features = [f for f in current_features if f not in to_remove]

        log_records.append({
            'iter': iter_idx,
            'noise_th_cls': noise_cls,
            'noise_th_reg': noise_reg,
            'noise_th_dir': noise_dir,
            'removed_count': len(to_remove),
            'remaining_count': len(current_features)
        })

        logger.info(f"轮次 {iter_idx:2d} | 噪声阈值(分类/回归/方向): {noise_cls:3d} / "
              f"{noise_reg:3d} / {noise_dir:3d} | "
              f"剔除 {len(to_remove):2d} 个 | 剩余: {len(current_features):3d}")
        if to_remove and len(to_remove) <= 20:
            logger.info(f"       剔除: {to_remove}")
        elif to_remove:
            logger.info(f"       剔除前10个: {to_remove[:10]} ...")

    # 保存筛选结果
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
        result = {
            'selected_features': current_features,
            'original_count': len(feature_names),
            'final_count': len(current_features),
            'log_records': log_records
        }
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        logger.info(f"筛选结果已保存至: {output_path}")

    logger.info(f"探针法筛选完成！保留 {len(current_features)} / {len(feature_names)} 个特征")
    return current_features, log_records


def load_selected_features(path):
    """加载已保存的筛选结果"""
    with open(path, 'r', encoding='utf-8') as f:
        result = json.load(f)
    return result['selected_features']
