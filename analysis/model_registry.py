# -*- coding: utf-8 -*-
"""
模型注册中心 (Model Registry)

统一管理所有模型的元数据、保存和加载。

每个保存的模型包含两部分：
    1. 模型文件 (.pkl / .pt) ── 模型权重 + scaler
    2. 元数据文件 (.meta.json) ── 训练信息、特征版本、评价指标

元数据结构：
    {
        "model_name": "xgboost_classifier",
        "model_version": "1.0.0",
        "model_type": "xgboost",
        "train_start_date": "2024-01-01",
        "train_end_date": "2024-12-31",
        "feature_version": "2.0.0",
        "feature_names": ["ma5", "rsi", ...],
        "n_features": 42,
        "metrics": {"accuracy": 0.55, "f1": 0.52},
        "params": {"max_depth": 6, "learning_rate": 0.1},
        "seed": 42,
        "created_at": "2025-01-15T14:30:00",
        "model_file": "xgboost_classifier_20250115.pkl",
    }
"""

import os
import json
import pickle
import numpy as np
from datetime import datetime
from config.logging_config import get_logger

logger = get_logger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)

# 模型版本
MODEL_VERSION = "1.0.0"


def build_metadata(
    model_name: str,
    model_type: str,
    feature_names: list,
    train_start_date=None,
    train_end_date=None,
    metrics: dict = None,
    params: dict = None,
    seed: int = 42,
    feature_version: str = None,
    model_file: str = None,
    extra: dict = None,
) -> dict:
    """
    构建标准化模型元数据。

    Args:
        model_name: 模型名称（如 xgboost_classifier, ranking_lgb）
        model_type: 模型类型（如 xgboost, lightgbm, transformer）
        feature_names: 使用的特征列表
        train_start_date: 训练数据起始日期
        train_end_date: 训练数据结束日期
        metrics: 评价指标字典
        params: 模型超参数
        seed: 随机种子
        feature_version: 特征工程版本号
        model_file: 模型文件名
        extra: 额外元数据

    Returns:
        dict: 标准化元数据
    """
    if feature_version is None:
        try:
            from data_processor.feature_engineer import FEATURE_VERSION
            feature_version = FEATURE_VERSION
        except ImportError:
            feature_version = "unknown"

    metadata = {
        'model_name': model_name,
        'model_version': MODEL_VERSION,
        'model_type': model_type,
        'train_start_date': _to_date_str(train_start_date),
        'train_end_date': _to_date_str(train_end_date),
        'feature_version': feature_version,
        'feature_names': list(feature_names),
        'n_features': len(feature_names),
        'metrics': _sanitize_metrics(metrics or {}),
        'params': params or {},
        'seed': seed,
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'model_file': model_file,
    }

    if extra:
        metadata.update(extra)

    return metadata


def save_model(model_obj, metadata: dict, model_name: str = None) -> str:
    """
    保存模型和元数据到磁盘。

    生成两个文件:
        - {model_name}.pkl      模型权重
        - {model_name}.meta.json 元数据

    Args:
        model_obj: 训练好的模型对象
        metadata: build_metadata() 返回的元数据
        model_name: 文件名前缀，None 则自动生成

    Returns:
        str: 模型文件路径
    """
    if model_name is None:
        model_name = f"{metadata.get('model_type', 'model')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    model_file = f"{model_name}.pkl"
    meta_file = f"{model_name}.meta.json"

    model_path = os.path.join(MODEL_DIR, model_file)
    meta_path = os.path.join(MODEL_DIR, meta_file)

    # 保存模型
    with open(model_path, 'wb') as f:
        pickle.dump(model_obj, f)

    # 更新元数据中的模型文件名
    metadata['model_file'] = model_file

    # 保存元数据
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2, default=str)

    logger.info("模型已保存: %s", model_path)
    logger.info("元数据已保存: %s", meta_path)
    _log_metadata_summary(metadata)

    return model_path


def load_model(model_path: str = None, model_type: str = None) -> tuple:
    """
    加载模型和元数据。

    Args:
        model_path: 模型文件路径（.pkl）。若为 None，自动加载最新模型。
        model_type: 当 model_path=None 时，按类型筛选最新模型。

    Returns:
        (model_obj, metadata): 模型对象 + 元数据字典
    """
    if model_path is None:
        model_path = _find_latest_model(model_type)
        if model_path is None:
            logger.error("无可用模型（type=%s）", model_type)
            return None, None

    # 加载模型
    with open(model_path, 'rb') as f:
        model_obj = pickle.load(f)

    # 加载元数据
    meta_path = model_path.replace('.pkl', '.meta.json')
    metadata = {}
    if os.path.exists(meta_path):
        with open(meta_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
    else:
        logger.warning("元数据文件不存在: %s，模型可能是旧版本保存的", meta_path)

    logger.info("模型已加载: %s", os.path.basename(model_path))
    if metadata:
        _log_metadata_summary(metadata)

    return model_obj, metadata


def list_models(model_type: str = None) -> list:
    """
    列出所有已保存的模型及其元数据。

    Args:
        model_type: 按类型筛选

    Returns:
        list[dict]: 元数据列表（按创建时间降序）
    """
    models = []
    for f in os.listdir(MODEL_DIR):
        if not f.endswith('.meta.json'):
            continue
        if model_type and model_type not in f:
            continue

        meta_path = os.path.join(MODEL_DIR, f)
        try:
            with open(meta_path, 'r', encoding='utf-8') as fp:
                meta = json.load(fp)
                models.append(meta)
        except Exception as e:
            logger.warning("读取元数据失败: %s - %s", f, e)

    models.sort(key=lambda m: m.get('created_at', ''), reverse=True)
    return models


# ═══════════════════════════════════════════════════════════════════════════════
#  内部辅助
# ═══════════════════════════════════════════════════════════════════════════════

def _to_date_str(d) -> str:
    """将各种日期格式统一为字符串。"""
    if d is None:
        return None
    if isinstance(d, str):
        return d
    if hasattr(d, 'strftime'):
        return d.strftime('%Y-%m-%d')
    if hasattr(d, 'item'):  # numpy datetime64
        return str(pd.Timestamp(d).strftime('%Y-%m-%d'))
    return str(d)


def _sanitize_metrics(metrics: dict) -> dict:
    """确保 metrics 中的值可 JSON 序列化。"""
    clean = {}
    for k, v in metrics.items():
        if isinstance(v, (np.floating, np.integer)):
            clean[k] = float(v)
        elif isinstance(v, (np.ndarray,)):
            clean[k] = v.tolist()
        elif isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
            clean[k] = None
        else:
            clean[k] = v
    return clean


def _find_latest_model(model_type: str = None) -> str:
    """查找最新的模型文件路径。"""
    pattern = f"{model_type}_" if model_type else ""
    files = sorted([
        f for f in os.listdir(MODEL_DIR)
        if f.endswith('.pkl') and f.startswith(pattern)
    ], reverse=True)

    if not files:
        return None

    return os.path.join(MODEL_DIR, files[0])


def _log_metadata_summary(metadata: dict):
    """在日志中输出元数据摘要。"""
    logger.info(
        "  模型: %s v%s | 类型: %s | 特征: %d 个 (v%s) | 训练: %s ~ %s",
        metadata.get('model_name', '?'),
        metadata.get('model_version', '?'),
        metadata.get('model_type', '?'),
        metadata.get('n_features', 0),
        metadata.get('feature_version', '?'),
        metadata.get('train_start_date', '?'),
        metadata.get('train_end_date', '?'),
    )
    metrics = metadata.get('metrics', {})
    if metrics:
        logger.info("  评价指标: %s", metrics)


# ═══════════════════════════════════════════════════════════════════════════════
#  随机种子固定工具
# ═══════════════════════════════════════════════════════════════════════════════

def set_global_seed(seed: int = 42):
    """
    固定所有随机种子，确保实验可复现。

    覆盖: Python random, NumPy, PyTorch (如已安装)
    """
    import random
    random.seed(seed)
    np.random.seed(seed)

    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
            torch.backends.cudnn.deterministic = True
            torch.backends.cudnn.benchmark = False
    except ImportError:
        pass

    try:
        import tensorflow as tf
        tf.random.set_seed(seed)
    except ImportError:
        pass

    logger.info("全局随机种子已固定: seed=%d", seed)
