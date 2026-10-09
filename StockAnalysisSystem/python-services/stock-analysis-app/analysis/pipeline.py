# -*- coding: utf-8 -*-
"""
训练管线 (Training Pipeline)

核心原则：
    1. 未来数据只能作为训练标签，绝不能进入线上预测特征
    2. 特征表和训练样本表在逻辑上分开
    3. 标准化/归一化参数只能用训练集拟合
    4. 训练集/验证集/测试集必须按日期划分，绝不随机打乱时间

使用方式：
    from analysis.pipeline import build_training_samples, fit_scaler_on_train

    # 1. 从特征面板构建训练样本（自动添加标签 + 时间切分）
    X_train, X_val, X_test, y_train, y_val, y_test, meta = build_training_samples(
        panel_df,
        feature_cols=['ma5', 'rsi', 'macd_dif', ...],
        target='future_return_5d',
        train_ratio=0.6, val_ratio=0.2, test_ratio=0.2,
    )

    # 2. 标准化（仅用训练集拟合）
    X_train, X_val, X_test, scaler = fit_scaler_on_train(X_train, X_val, X_test)
"""

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from config.logging_config import get_logger

logger = get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
#  标签列定义 ── 这些列只能作为训练目标，绝不能作为模型输入特征
# ═══════════════════════════════════════════════════════════════════════════════

LABEL_COLUMNS = frozenset({
    'future_return_1d',
    'future_return_5d',
    'future_direction_1d',
    'future_direction_5d',
    'label',
})
"""
不可作为特征的列集合。

任何代码路径在构造模型输入 X 时，必须先过滤掉这些列。
这是防泄漏的第一道防线。
"""

# 标识列 ── 非数值型或无预测意义的列
IDENTITY_COLUMNS = frozenset({
    'ts_code', 'trade_date', '日期', '股票名称', 'name', 'symbol',
    'instrument', 'is_val', 'label_target_date',
})


# ═══════════════════════════════════════════════════════════════════════════════
#  标签计算 ── 只在训练样本构建阶段调用
# ═══════════════════════════════════════════════════════════════════════════════

def compute_labels(df: pd.DataFrame, forward_days: int = 5) -> pd.DataFrame:
    """
    为面板数据计算训练标签（未来收益率）。

    这些列只应在构建训练样本时使用，绝不能存入 stock_features 表
    或用于线上预测。

    Args:
        df: 面板数据（需含 ts_code, close 列）
        forward_days: 远期天数

    Returns:
        pd.DataFrame: 添加了 label 列的副本（不修改传入对象）
    """
    df = df.copy()
    df = df.sort_values(['ts_code', 'trade_date'])

    close = df.groupby('ts_code')['close']

    # 主标签：未来 N 日收益率
    df['label'] = close.transform(lambda x: x.shift(-forward_days) / x - 1)

    # 辅助标签
    df['future_return_1d'] = close.transform(lambda x: x.shift(-1) / x - 1)
    df['future_return_5d'] = close.transform(lambda x: x.shift(-5) / x - 1)
    df['future_direction_1d'] = (df['future_return_1d'] > 0).astype(float).where(df['future_return_1d'].notna())
    df['future_direction_5d'] = (df['future_return_5d'] > 0).astype(float).where(df['future_return_5d'].notna())

    valid = df['label'].notna().sum()
    logger.info("标签计算完成: %d 行有效（forward_days=%d）", valid, forward_days)
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  特征过滤 ── 强制移除标签列和标识列
# ═══════════════════════════════════════════════════════════════════════════════

def sanitize_feature_columns(feature_cols: list, df_columns=None) -> list:
    """
    过滤特征列，强制移除标签列和标识列。

    这是防泄漏的第二道防线：即使调用方不小心传入了 future_return 等列，
    此函数也会静默移除并记录警告。

    Args:
        feature_cols: 候选特征列名列表
        df_columns: DataFrame 实际列名（用于验证列是否存在）

    Returns:
        list: 过滤后的安全特征列名
    """
    forbidden = LABEL_COLUMNS | IDENTITY_COLUMNS
    safe = []
    removed = []

    for col in feature_cols:
        if col in forbidden:
            removed.append(col)
        elif df_columns is not None and col not in df_columns:
            continue  # 不存在于 DataFrame 中，跳过
        else:
            safe.append(col)

    if removed:
        logger.warning("以下标签/标识列已从特征中移除（防泄漏）: %s", removed)

    return safe


# ═══════════════════════════════════════════════════════════════════════════════
#  时间切分 ── 按日期严格划分，不随机打乱
# ═══════════════════════════════════════════════════════════════════════════════

def time_split(
    df: pd.DataFrame,
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    test_ratio: float = 0.2,
    date_col: str = 'trade_date',
) -> tuple:
    """
    按时间顺序将面板数据切分为训练/验证/测试集。

    切分在「日期」级别进行（非行级别），确保同一天的所有股票
    不会被分到不同的集合中。

    Args:
        df: 面板数据
        train_ratio: 训练集占比
        val_ratio: 验证集占比
        test_ratio: 测试集占比
        date_col: 日期列名

    Returns:
        (train_df, val_df, test_df): 三个 DataFrame

    Raises:
        ValueError: 比例之和不为 1
    """
    if abs(train_ratio + val_ratio + test_ratio - 1.0) > 1e-6:
        raise ValueError(
            f"比例之和必须为 1，当前: {train_ratio + val_ratio + test_ratio:.4f}"
        )

    df = df.sort_values(date_col).copy()
    unique_dates = sorted(df[date_col].unique())
    n_dates = len(unique_dates)

    train_end_idx = int(n_dates * train_ratio)
    val_end_idx = int(n_dates * (train_ratio + val_ratio))

    train_dates = set(unique_dates[:train_end_idx])
    val_dates = set(unique_dates[train_end_idx:val_end_idx])
    test_dates = set(unique_dates[val_end_idx:])

    train_df = df[df[date_col].isin(train_dates)].copy()
    val_df = df[df[date_col].isin(val_dates)].copy()
    test_df = df[df[date_col].isin(test_dates)].copy()

    logger.info(
        "时间切分完成: train=%d天(%d行), val=%d天(%d行), test=%d天(%d行)",
        len(train_dates), len(train_df),
        len(val_dates), len(val_df),
        len(test_dates), len(test_df),
    )

    # 记录切分边界（用于审计）
    if train_dates:
        logger.info("  训练集: %s ~ %s", min(train_dates), max(train_dates))
    if val_dates:
        logger.info("  验证集: %s ~ %s", min(val_dates), max(val_dates))
    if test_dates:
        logger.info("  测试集: %s ~ %s", min(test_dates), max(test_dates))

    return train_df, val_df, test_df


# ═══════════════════════════════════════════════════════════════════════════════
#  构建训练样本 ── 一站式入口
# ═══════════════════════════════════════════════════════════════════════════════

def build_training_samples(
    panel_df: pd.DataFrame,
    feature_cols: list,
    target: str = 'future_return_5d',
    train_ratio: float = 0.6,
    val_ratio: float = 0.2,
    test_ratio: float = 0.2,
    forward_days: int = 5,
    date_col: str = 'trade_date',
) -> dict:
    """
    一站式训练样本构建：标签计算 → 特征过滤 → 时间切分 → NaN 清理。

    流程:
        1. 添加标签（compute_labels）
        2. 过滤特征列（sanitize_feature_columns，移除标签/标识列）
        3. 按日期切分为 train/val/test（time_split）
        4. 清理 NaN/Inf
        5. 返回 X, y 矩阵和元信息

    Args:
        panel_df: 特征面板（来自 feature_engineer 的输出，不含标签）
        feature_cols: 候选特征列名
        target: 标签列名（必须在 LABEL_COLUMNS 中）
        train_ratio: 训练集占比
        val_ratio: 验证集占比
        test_ratio: 测试集占比
        forward_days: 远期收益率天数
        date_col: 日期列名

    Returns:
        dict: {
            'X_train', 'X_val', 'X_test': np.ndarray,
            'y_train', 'y_val', 'y_test': np.ndarray,
            'feature_names': list[str],
            'target': str,
            'split_info': dict,  # 切分日期范围
        }
    """
    if target not in LABEL_COLUMNS and target != 'label':
        logger.warning(
            "目标列 '%s' 不在 LABEL_COLUMNS 中，请确认它确实是标签而非特征",
            target,
        )

    # 1. 添加标签
    labeled_df = compute_labels(panel_df, forward_days=forward_days)

    # 2. 过滤特征
    safe_features = sanitize_feature_columns(feature_cols, labeled_df.columns)
    if not safe_features:
        logger.error("过滤后无可用特征列")
        return {}

    # 3. 时间切分
    train_df, val_df, test_df = time_split(
        labeled_df, train_ratio, val_ratio, test_ratio, date_col
    )

    # 4. 提取 X, y 并清理
    def _extract_xy(split_df):
        sub = split_df[safe_features + [target]].replace([np.inf, -np.inf], np.nan)
        sub = sub.dropna()
        return sub[safe_features].values, sub[target].values

    X_train, y_train = _extract_xy(train_df)
    X_val, y_val = _extract_xy(val_df)
    X_test, y_test = _extract_xy(test_df)

    logger.info(
        "样本构建完成: train=%d, val=%d, test=%d, features=%d",
        len(X_train), len(X_val), len(X_test), len(safe_features),
    )

    return {
        'X_train': X_train, 'y_train': y_train,
        'X_val': X_val, 'y_val': y_val,
        'X_test': X_test, 'y_test': y_test,
        'feature_names': safe_features,
        'target': target,
        'split_info': {
            'train_dates': (train_df[date_col].min(), train_df[date_col].max()),
            'val_dates': (val_df[date_col].min(), val_df[date_col].max()),
            'test_dates': (test_df[date_col].min(), test_df[date_col].max()),
        },
    }


# ═══════════════════════════════════════════════════════════════════════════════
#  标准化 ── 仅在训练集上拟合
# ═══════════════════════════════════════════════════════════════════════════════

def fit_scaler_on_train(
    X_train: np.ndarray,
    X_val: np.ndarray = None,
    X_test: np.ndarray = None,
    scaler_type: str = 'standard',
) -> tuple:
    """
    在训练集上拟合标准化器，然后分别变换训练/验证/测试集。

    [防泄漏] scaler.fit() 只使用 X_train，绝不接触 X_val / X_test。

    Args:
        X_train: 训练集特征矩阵
        X_val: 验证集特征矩阵（可选）
        X_test: 测试集特征矩阵（可选）
        scaler_type: 'standard' (StandardScaler) 或 'minmax' (MinMaxScaler)

    Returns:
        (X_train_scaled, X_val_scaled, X_test_scaled, scaler)
        如果 X_val/X_test 为 None，对应返回 None
    """
    if scaler_type == 'standard':
        scaler = StandardScaler()
    elif scaler_type == 'minmax':
        from sklearn.preprocessing import MinMaxScaler
        scaler = MinMaxScaler()
    else:
        raise ValueError(f"不支持的 scaler 类型: {scaler_type}")

    # 仅在训练集上拟合
    scaler.fit(X_train)
    X_train_scaled = scaler.transform(X_train)

    X_val_scaled = scaler.transform(X_val) if X_val is not None else None
    X_test_scaled = scaler.transform(X_test) if X_test is not None else None

    logger.info(
        "Scaler 拟合完成（仅训练集）: mean=%s, std=%s",
        np.round(scaler.mean_[:3], 4) if hasattr(scaler, 'mean_') else 'N/A',
        np.round(scaler.scale_[:3], 4) if hasattr(scaler, 'scale_') else 'N/A',
    )

    return X_train_scaled, X_val_scaled, X_test_scaled, scaler
