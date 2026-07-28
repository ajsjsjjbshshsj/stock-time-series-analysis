# -*- coding: utf-8 -*-
"""
Transformer 模型配置参数

基于 app/code/src/config.py 改造，适配 StockAnalysisSystem 目录结构。
"""
import os

# 项目根目录
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SEQUENCE_LENGTH = 60
FEATURE_NUM = '158+39'

TRANSFORMER_CONFIG = {
    # 序列和模型结构
    'sequence_length': SEQUENCE_LENGTH,
    'd_model': 128,
    'nhead': 4,
    'num_layers': 2,
    'dim_feedforward': 256,
    'dropout': 0.1,
    'feature_num': FEATURE_NUM,
    'max_grad_norm': 5.0,

    # 训练参数
    'batch_size': 2,
    'num_epochs': 50,
    'learning_rate': 1e-5,
    'drop_clip': True,

    # 排序损失权重
    'pairwise_weight': 1,
    'base_weight': 1.0,
    'top5_weight': 2.0,

    # 路径配置
    'output_dir': os.path.join(PROJECT_DIR, 'models', 'transformer', f'{SEQUENCE_LENGTH}_{FEATURE_NUM}'),
    'data_path': os.path.join(PROJECT_DIR, 'data'),

    # 探针法特征筛选
    'use_probe_selection': True,
    'probe_n_iter': 10,
    'probe_n_noise': 10,
    'probe_selected_features_path': None,

    # 多头多任务训练
    'use_multi_head': True,
    'reg_weight': 0.1,
    'cls_weight': 0.1,
    'dir_weight': 0.1,

    # 预测时动态权重
    'weight_temperature': 0.5,

    # 板块强度筛选
    'use_sector_filter': False,
    'sector_filter_path': os.path.join(PROJECT_DIR, 'output', 'top5_sectors.json'),
    'sector_top_n': 5,

    # 随机种子
    'seed': 42,
}
