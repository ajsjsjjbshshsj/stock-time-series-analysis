# -*- coding: utf-8 -*-
"""
Transformer 训练和预测入口

基于 app/code/src/train.py 和 train.py 的训练逻辑整合，
适配 StockAnalysisSystem 的调用方式。
"""
import os
import json
import random
import multiprocessing as mp
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from sklearn.preprocessing import StandardScaler
from tqdm import tqdm
from config.logging_config import get_logger
logger = get_logger(__name__)
from analysis.transformer_config import TRANSFORMER_CONFIG
from analysis.transformer_model import StockTransformer, MultiHeadStockTransformer
from analysis.transformer_utils import (
    FEATURE_COLUMNS_MAP,
    FEATURE_ENGINEER_FUNC_MAP,
    engineer_features_39,
    engineer_features_158plus39,
    add_cross_sectional_features,
    create_ranking_dataset_vectorized,
)

# ============================================================
# 工具函数
# ============================================================

def set_seed(seed=42):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    os.environ['PYTHONHASHSEED'] = str(seed)


def _build_label_and_clean(processed, drop_small_open=True):
    """统一构建标签并清洗无效样本。"""
    processed['open_t1'] = processed.groupby('股票代码')['开盘'].shift(-1)
    processed['open_t5'] = processed.groupby('股票代码')['开盘'].shift(-5)

    if drop_small_open:
        processed = processed[processed['open_t1'] > 1e-4]

    processed['label'] = (processed['open_t5'] - processed['open_t1']) / (processed['open_t1'] + 1e-12)
    processed = processed.dropna(subset=['label'])
    processed.drop(columns=['open_t1', 'open_t5'], inplace=True)
    return processed


def _compute_new_features_for_stock_worker(args):
    """
    模块级 worker 函数，用于 multiprocessing。
    args: (stock_df, date_col, feature_engineer_func)
    """
    stock_df, date_col, feature_engineer_func = args
    stock_df = stock_df.copy()
    stock_df[date_col] = pd.to_datetime(stock_df[date_col])
    stock_df = stock_df.sort_values(date_col).reset_index(drop=True)
    n = len(stock_df)
    if n < 10:
        return pd.DataFrame()
    # 使用全部数据（含预热）计算特征
    result = feature_engineer_func(stock_df)
    # 只保留新增日期（去除预热窗口）
    new_first = stock_df[date_col].iloc[0] if date_col in stock_df.columns else stock_df['日期'].iloc[0]
    result = result[result['日期'] >= new_first]
    return result


def _preprocess_common(df, stockid2idx, desc, drop_small_open=True, config=None):
    """通用特征工程流程。"""
    if config is None:
        config = TRANSFORMER_CONFIG

    feature_num = config['feature_num']
    assert feature_num in FEATURE_ENGINEER_FUNC_MAP, f"Unsupported feature_num: {feature_num}"
    feature_engineer = FEATURE_ENGINEER_FUNC_MAP[feature_num]
    feature_columns = FEATURE_COLUMNS_MAP[feature_num]

    df = df.copy()
    df = df.sort_values(['股票代码', '日期']).reset_index(drop=True)

    logger.info(f"正在逐股进行{desc}...")
    groups = [group for _, group in df.groupby('股票代码', sort=False)]
    if len(groups) == 0:
        raise ValueError(f"{desc}输入为空，无法继续")

    processed_list = []
    for g in tqdm(groups, desc=f"{desc}"):
        try:
            processed_list.append(feature_engineer(g))
        except Exception as e:
            logger.warning(f"跳过股票特征计算: {e}")

    processed_list = [p for p in processed_list if p is not None and not p.empty]
    if not processed_list:
        raise ValueError(f"{desc}所有股票均未成功计算特征")

    # 分块 concat 避免一次性合并大量 DataFrame 导致内存爆炸
    chunk_size = 500
    chunks = []
    for i in range(0, len(processed_list), chunk_size):
        chunks.append(pd.concat(processed_list[i:i+chunk_size], ignore_index=True))
    processed = pd.concat(chunks, ignore_index=True) if len(chunks) > 1 else chunks[0]
    del chunks
    del processed_list

    processed = add_cross_sectional_features(processed)

    processed['instrument'] = processed['股票代码'].map(stockid2idx)
    processed = processed.dropna(subset=['instrument']).copy()
    processed['instrument'] = processed['instrument'].astype(np.int64)

    processed = _build_label_and_clean(processed, drop_small_open=drop_small_open)
    return processed, feature_columns


def preprocess_data(df, is_train=True, stockid2idx=None, config=None):
    if not is_train:
        return _preprocess_common(df, stockid2idx, desc="特征工程", drop_small_open=False, config=config)
    return _preprocess_common(df, stockid2idx, desc="特征工程", drop_small_open=True, config=config)


def preprocess_val_data(df, stockid2idx=None, config=None):
    return _preprocess_common(df, stockid2idx, desc="验证集特征工程", drop_small_open=True, config=config)


def _normalize_input_df(df):
    """统一处理输入 DataFrame 的列名和衍生列。"""
    df = df.copy()

    # 防御：去除重复列名
    if df.columns.duplicated().any():
        dup = df.columns[df.columns.duplicated()].tolist()
        logger.warning(f"_normalize_input_df: 输入有重复列名 {dup}，已去重")
        df = df.loc[:, ~df.columns.duplicated()]

    if 'ts_code' in df.columns and '股票代码' not in df.columns:
        df.rename(columns={'ts_code': '股票代码'}, inplace=True)
    if 'trade_date' in df.columns and '日期' not in df.columns:
        df.rename(columns={'trade_date': '日期'}, inplace=True)
    col_map = {}
    if 'open' in df.columns and '开盘' not in df.columns:
        col_map['open'] = '开盘'
    if 'high' in df.columns and '最高' not in df.columns:
        col_map['high'] = '最高'
    if 'low' in df.columns and '最低' not in df.columns:
        col_map['low'] = '最低'
    if 'close' in df.columns and '收盘' not in df.columns:
        col_map['close'] = '收盘'
    if 'vol' in df.columns and '成交量' not in df.columns:
        col_map['vol'] = '成交量'
    if 'amount' in df.columns and '成交额' not in df.columns:
        col_map['amount'] = '成交额'
    if col_map:
        df.rename(columns=col_map, inplace=True)

    # 再次检查重命名后是否有重复
    if df.columns.duplicated().any():
        dup = df.columns[df.columns.duplicated()].tolist()
        logger.warning(f"_normalize_input_df: 重命名后产生重复列名 {dup}，已去重")
        df = df.loc[:, ~df.columns.duplicated()]

    # 衍生列
    df['prev_close'] = df.groupby('股票代码')['收盘'].shift(1)
    df['涨跌额'] = df['收盘'] - df['prev_close']
    df['涨跌幅'] = (df['涨跌额'] / (df['prev_close'] + 1e-12)) * 100
    df['振幅'] = ((df['最高'] - df['最低']) / (df['prev_close'] + 1e-12)) * 100
    df.drop(columns=['prev_close'], inplace=True)
    return df


def _prepare_for_parquet(df):
    """Clean mixed object columns before writing with pyarrow."""
    df = df.copy()
    if df.columns.duplicated().any():
        dup = df.columns[df.columns.duplicated()].tolist()
        logger.warning(f"_prepare_for_parquet: 输入有重复列名 {dup}，已去重")
        df = df.loc[:, ~df.columns.duplicated()]

    for col in df.columns:
        if pd.api.types.is_object_dtype(df[col]) or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].where(df[col].notna(), pd.NA).astype('string')
    return df


def compute_and_save_features(panel_df, save_path=None, config=None, use_parallel=True,
                              n_workers=None):
    """Build raw-unit features; existing raw observations are merged before rebuild."""
    from analysis.transformer_features import save_feature_cache
    config = config if config is not None else TRANSFORMER_CONFIG
    save_path = save_path or os.path.join(config['output_dir'], f"features_{config['feature_num']}.parquet")
    incremental = os.path.exists(save_path) and os.path.exists(os.path.join(os.path.dirname(os.path.abspath(save_path)), 'raw_panel.parquet'))
    return save_feature_cache(panel_df, save_path, config, use_parallel, n_workers, incremental)


def load_precomputed_features(save_path, config=None):
    """Load version-2 RAW features; scaling belongs to a particular trained model."""
    from analysis.transformer_features import load_feature_cache
    return load_feature_cache(save_path, config if config is not None else TRANSFORMER_CONFIG)


def compute_and_save_features_incremental(new_panel_df, save_path=None, config=None,
                                          use_parallel=True, n_workers=None):
    """Merge raw stock/date input and rebuild full retained history, not 80-row tails."""
    from analysis.transformer_features import save_feature_cache
    config = config if config is not None else TRANSFORMER_CONFIG
    save_path = save_path or os.path.join(config['output_dir'], f"features_{config['feature_num']}.parquet")
    return save_feature_cache(new_panel_df, save_path, config, use_parallel, n_workers, incremental=True)


# ============================================================
# 损失函数
# ============================================================

class WeightedRankingLoss(nn.Module):
    """组合的加权排序损失函数，着重强调top-k的样本。"""
    def __init__(self, temperature=1.0, k=5, weight_factor=2.0, pairwise_weight=1, base_weight=1.0):
        super(WeightedRankingLoss, self).__init__()
        self.temperature = temperature
        self.k = k
        self.weight_factor = weight_factor
        self.pairwise_weight = pairwise_weight
        self.base_weight = base_weight

    def listwise_loss(self, y_pred, y_true, weights):
        pred_probs = F.softmax(y_pred / self.temperature, dim=1)
        target_probs = F.softmax(y_true / self.temperature, dim=1)
        weighted_ce = -(target_probs * torch.log(pred_probs + 1e-12) * weights)
        ce_loss = (weighted_ce.sum(dim=1) / (weights.sum(dim=1) + 1e-12)).mean()
        return ce_loss

    def pairwise_loss(self, y_pred, y_true, weights):
        batch_size, num_items = y_pred.size()
        pred_diff = y_pred.unsqueeze(2) - y_pred.unsqueeze(1)
        true_diff = y_true.unsqueeze(2) - y_true.unsqueeze(1)
        mask = (true_diff != 0).float()
        weight_matrix = weights.unsqueeze(2) + weights.unsqueeze(1)
        pairwise_loss = torch.sigmoid(-pred_diff * torch.sign(true_diff))
        weighted_loss = pairwise_loss * mask * weight_matrix
        num_pairs = mask.sum(dim=[1, 2]).clamp(min=1)
        loss = (weighted_loss.sum(dim=[1, 2]) / num_pairs).mean()
        return loss

    def forward(self, y_pred, y_true):
        batch_size, num_items = y_true.size()
        k = min(self.k, num_items)
        _, top_indices = torch.topk(y_true, k, dim=1)
        weights = torch.full_like(y_true, fill_value=self.base_weight)
        for i in range(batch_size):
            weights[i, top_indices[i]] = self.weight_factor
        listwise = self.listwise_loss(y_pred, y_true, weights)
        pairwise = self.pairwise_loss(y_pred, y_true, weights)
        total_loss = listwise + self.pairwise_weight * pairwise
        return total_loss


class MultiTaskRankingLoss(nn.Module):
    """多任务损失：ranking + regression + classification + direction。"""
    def __init__(self, ranking_loss, reg_weight=0.1, cls_weight=0.1, dir_weight=0.1):
        super().__init__()
        self.ranking_loss = ranking_loss
        self.mse_loss = nn.MSELoss()
        self.bce_loss = nn.BCELoss()
        self.reg_weight = reg_weight
        self.cls_weight = cls_weight
        self.dir_weight = dir_weight

    def forward(self, outputs, targets, masks, label_cls=None, label_dir=None):
        ranking = self.ranking_loss(outputs['ranking'], targets)
        valid = masks.view(-1).bool()
        reg = self.mse_loss(outputs['regression'].view(-1)[valid], targets.view(-1)[valid])

        if label_cls is not None:
            cls = self.bce_loss(outputs['classification'].view(-1)[valid], label_cls.view(-1)[valid])
        else:
            cls = torch.tensor(0.0, device=outputs['ranking'].device)

        if label_dir is not None:
            direction = self.bce_loss(outputs['direction'].view(-1)[valid], label_dir.view(-1)[valid])
        else:
            direction = torch.tensor(0.0, device=outputs['ranking'].device)

        total = (ranking + self.reg_weight * reg + self.cls_weight * cls + self.dir_weight * direction)
        return total, ranking.item(), reg.item(), cls.item(), direction.item()


# ============================================================
# 评估指标
# ============================================================

def calculate_ranking_metrics(y_pred, y_true, masks, k=5):
    """计算评估指标：Top 5 收益之和，以及与理论最高值和随机值的比值"""
    batch_size = y_pred.size(0)
    pred_return_sum_list = []
    max_return_sum_list = []
    random_return_sum_list = []
    ratio_pred_list = []
    ratio_random_list = []
    final_score_list = []

    for i in range(batch_size):
        mask = masks[i]
        valid_indices = mask.nonzero().squeeze()
        if valid_indices.numel() < k:
            continue

        valid_pred = y_pred[i][valid_indices]
        valid_true = y_true[i][valid_indices]

        _, pred_indices = torch.topk(valid_pred, k)
        pred_top_returns = valid_true[pred_indices]
        pred_return_sum = pred_top_returns.sum().item()

        _, true_indices = torch.topk(valid_true, k)
        true_top_returns = valid_true[true_indices]
        max_return_sum = true_top_returns.sum().item()

        random_return_sum = k * valid_true.mean().item()

        ratio_pred = pred_return_sum / (max_return_sum + 1e-12) if abs(max_return_sum) > 1e-9 else 0.0
        ratio_random = random_return_sum / (max_return_sum + 1e-12) if abs(max_return_sum) > 1e-9 else 0.0
        denominator = max_return_sum - random_return_sum
        final_score = (pred_return_sum - random_return_sum) / (denominator + 1e-12) if abs(denominator) > 1e-6 else 0.0

        pred_return_sum_list.append(pred_return_sum)
        max_return_sum_list.append(max_return_sum)
        random_return_sum_list.append(random_return_sum)
        ratio_pred_list.append(ratio_pred)
        ratio_random_list.append(ratio_random)
        final_score_list.append(final_score)

    return {
        'pred_return_sum': np.mean(pred_return_sum_list) if pred_return_sum_list else 0.0,
        'max_return_sum': np.mean(max_return_sum_list) if max_return_sum_list else 0.0,
        'random_return_sum': np.mean(random_return_sum_list) if random_return_sum_list else 0.0,
        'ratio_pred': np.mean(ratio_pred_list) if ratio_pred_list else 0.0,
        'ratio_random': np.mean(ratio_random_list) if ratio_random_list else 0.0,
        'final_score': np.mean(final_score_list) if final_score_list else 0.0,
    }


# ============================================================
# 数据集
# ============================================================

class RankingDataset(Dataset):
    """排序数据集"""
    def __init__(self, sequences, targets, relevance_scores, stock_indices,
                 cls_labels=None, dir_labels=None):
        self.sequences = sequences
        self.targets = targets
        self.relevance_scores = relevance_scores
        self.stock_indices = stock_indices
        self.cls_labels = cls_labels
        self.dir_labels = dir_labels

    def __len__(self):
        return len(self.sequences)

    def __getitem__(self, idx):
        item = {
            'sequences': torch.FloatTensor(self.sequences[idx]),
            'targets': torch.FloatTensor(self.targets[idx]),
            'relevance': torch.LongTensor(self.relevance_scores[idx]),
            'stock_indices': torch.LongTensor(self.stock_indices[idx])
        }
        if self.cls_labels is not None:
            item['label_cls'] = torch.FloatTensor(self.cls_labels[idx])
        if self.dir_labels is not None:
            item['label_dir'] = torch.FloatTensor(self.dir_labels[idx])
        return item


def collate_fn(batch):
    """自定义collate函数处理变长序列"""
    sequences = [item['sequences'] for item in batch]
    targets = [item['targets'] for item in batch]
    relevance = [item['relevance'] for item in batch]
    stock_indices = [item['stock_indices'] for item in batch]
    has_cls = 'label_cls' in batch[0]
    has_dir = 'label_dir' in batch[0]
    if has_cls:
        cls_labels = [item['label_cls'] for item in batch]
    if has_dir:
        dir_labels = [item['label_dir'] for item in batch]

    max_stocks = max(seq.size(0) for seq in sequences)
    padded_sequences, padded_targets, padded_relevance, padded_stock_indices, masks = [], [], [], [], []
    if has_cls:
        padded_cls = []
    if has_dir:
        padded_dir = []

    for i, (seq, tgt, rel, stock_idx) in enumerate(zip(sequences, targets, relevance, stock_indices)):
        num_stocks = seq.size(0)
        seq_len = seq.size(1)
        feature_dim = seq.size(2)

        if num_stocks < max_stocks:
            pad_size = max_stocks - num_stocks
            seq = torch.cat([seq, torch.zeros(pad_size, seq_len, feature_dim)], dim=0)
            tgt = torch.cat([tgt, torch.zeros(pad_size)], dim=0)
            rel = torch.cat([rel, torch.zeros(pad_size, dtype=torch.long)], dim=0)
            stock_idx = torch.cat([stock_idx, torch.zeros(pad_size, dtype=torch.long)], dim=0)

        mask = torch.ones(max_stocks)
        mask[num_stocks:] = 0

        padded_sequences.append(seq)
        padded_targets.append(tgt)
        padded_relevance.append(rel)
        padded_stock_indices.append(stock_idx)
        masks.append(mask)

        if has_cls:
            cls = cls_labels[i]
            if num_stocks < max_stocks:
                cls = torch.cat([cls, torch.zeros(max_stocks - num_stocks)])
            padded_cls.append(cls)
        if has_dir:
            d = dir_labels[i]
            if num_stocks < max_stocks:
                d = torch.cat([d, torch.zeros(max_stocks - num_stocks)])
            padded_dir.append(d)

    result = {
        'sequences': torch.stack(padded_sequences),
        'targets': torch.stack(padded_targets),
        'relevance': torch.stack(padded_relevance),
        'stock_indices': torch.stack(padded_stock_indices),
        'masks': torch.stack(masks)
    }
    if has_cls:
        result['label_cls'] = torch.stack(padded_cls)
    if has_dir:
        result['label_dir'] = torch.stack(padded_dir)
    return result


# ============================================================
# 训练 / 评估
# ============================================================

def train_ranking_model(model, dataloader, criterion, optimizer, device, epoch, config, writer=None):
    model.train()
    total_loss = 0
    total_metrics = {}
    local_step = 0
    use_multi_head = config.get('use_multi_head', False)

    for batch in tqdm(dataloader, desc=f"Training Epoch {epoch+1}"):
        sequences = batch['sequences'].to(device)
        targets = batch['targets'].to(device)
        masks = batch['masks'].to(device)

        optimizer.zero_grad()

        if use_multi_head:
            outputs = model(sequences, return_all_heads=True)
            label_cls = batch.get('label_cls', None)
            label_dir = batch.get('label_dir', None)
            if label_cls is not None:
                label_cls = label_cls.to(device)
            if label_dir is not None:
                label_dir = label_dir.to(device)

            batch_loss, rank_l, reg_l, cls_l, dir_l = criterion(outputs, targets, masks, label_cls, label_dir)
            masked_outputs = outputs['ranking'] * masks + (1 - masks) * (-1e9)
            masked_targets = targets * masks
        else:
            outputs = model(sequences)
            masked_outputs = outputs * masks + (1 - masks) * (-1e9)
            masked_targets = targets * masks
            batch_loss = criterion(masked_outputs, masked_targets)

        if batch_loss is not None:
            batch_loss.backward()
            if not config.get('drop_clip', True):
                torch.nn.utils.clip_grad_norm_(model.parameters(), config['max_grad_norm'])
            optimizer.step()
            total_loss += batch_loss.item()

            with torch.no_grad():
                metrics = calculate_ranking_metrics(masked_outputs, masked_targets, masks, k=5)
                for mk, mv in metrics.items():
                    total_metrics[mk] = total_metrics.get(mk, 0) + mv

            local_step += 1

    if local_step > 0:
        for mk in total_metrics:
            total_metrics[mk] /= local_step

    return total_loss / len(dataloader) if len(dataloader) > 0 else 0, total_metrics


def evaluate_ranking_model(model, dataloader, criterion, device, config, writer=None, epoch=None):
    model.eval()
    total_loss = 0
    total_metrics = {}
    num_batches = 0
    use_multi_head = config.get('use_multi_head', False)

    with torch.no_grad():
        for batch in tqdm(dataloader, desc=f"Evaluating"):
            sequences = batch['sequences'].to(device)
            targets = batch['targets'].to(device)
            masks = batch['masks'].to(device)

            if use_multi_head:
                outputs = model(sequences, return_all_heads=True)
                label_cls = batch.get('label_cls', None)
                label_dir = batch.get('label_dir', None)
                if label_cls is not None:
                    label_cls = label_cls.to(device)
                if label_dir is not None:
                    label_dir = label_dir.to(device)

                batch_loss, _, _, _, _ = criterion(outputs, targets, masks, label_cls, label_dir)
                masked_outputs = outputs['ranking'] * masks + (1 - masks) * (-1e9)
                masked_targets = targets * masks
            else:
                outputs = model(sequences)
                masked_outputs = outputs * masks + (1 - masks) * (-1e9)
                masked_targets = targets * masks
                batch_loss = criterion(masked_outputs, masked_targets)

            if batch_loss is not None:
                total_loss += batch_loss.item()

            metrics = calculate_ranking_metrics(masked_outputs, masked_targets, masks, k=5)
            for mk, mv in metrics.items():
                total_metrics[mk] = total_metrics.get(mk, 0) + mv
            num_batches += 1

    avg_loss = total_loss / num_batches if num_batches > 0 else 0
    for mk in total_metrics:
        total_metrics[mk] /= num_batches

    return avg_loss, total_metrics


# ============================================================
# 主训练函数
# ============================================================

def run_transformer_training(panel_df=None, feature_path=None, config=None, use_multi_head=True,
                              num_epochs=None, learning_rate=None, save_name=None):
    """
    执行 Transformer 训练流程。

    参数:
        panel_df: 原始面板数据（提供此参数时会先计算特征）
        feature_path: 预计算特征文件路径（提供此参数时跳过特征计算）
        config: 配置字典，None则使用默认
        use_multi_head: 是否使用多头模型
        num_epochs: 训练轮数，覆盖配置
        learning_rate: 学习率，覆盖配置
        save_name: 模型保存名称

    返回:
        dict: 包含模型路径、评估指标等
    """
    if config is None:
        config = TRANSFORMER_CONFIG.copy()
    else:
        config = {**TRANSFORMER_CONFIG, **config}

    if use_multi_head is not None:
        config['use_multi_head'] = use_multi_head
    if num_epochs is not None:
        config['num_epochs'] = num_epochs
    if learning_rate is not None:
        config['learning_rate'] = learning_rate

    # 设置随机种子
    set_seed(config.get('seed', 42))

    output_dir = config['output_dir']
    os.makedirs(output_dir, exist_ok=True)

    # 保存配置
    with open(os.path.join(output_dir, 'config.json'), 'w') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

    # 设置设备
    if torch.cuda.is_available():
        device = torch.device('cuda')
        logger.info("使用 CUDA 加速")
    elif torch.backends.mps.is_available():
        device = torch.device('mps')
        logger.info("使用 MPS 加速")
    else:
        device = torch.device('cpu')
        logger.info("使用 CPU 训练")

    # --- 数据准备：从原始数据或预计算特征加载 ---
    if feature_path and os.path.exists(feature_path):
        logger.info(f"从预计算特征文件加载: {feature_path}")
        all_data, features, stockid2idx, val_start_date = load_precomputed_features(feature_path, config)
        num_stocks = len(stockid2idx)

        # 数据已经在 compute_and_save_features 中标准化，直接使用
        # 如果 is_val 列不存在，根据 val_start_date 重建
        if 'is_val' not in all_data.columns:
            if val_start_date is not None:
                date_col = '日期' if '日期' in all_data.columns else 'trade_date'
                all_data['is_val'] = pd.to_datetime(all_data[date_col]) >= pd.Timestamp(val_start_date)
                logger.info(f"根据 val_start_date={val_start_date} 重建 is_val 标记")
            else:
                logger.warning("无 is_val 列且无 val_start_date，将全部数据作为训练集")
                all_data['is_val'] = False
        else:
            # parquet 中 is_val 可能存为字符串、整数等类型，统一安全转换
            if all_data['is_val'].dtype == object or str(all_data['is_val'].dtype) == 'string':
                all_data['is_val'] = all_data['is_val'].map(
                    lambda x: str(x).strip().lower() in ('true', '1', 'yes')
                )
            else:
                all_data['is_val'] = all_data['is_val'].astype(bool)

        train_data = all_data[~all_data['is_val']].copy().drop(columns=['is_val'])
        val_data = all_data[all_data['is_val']].copy().drop(columns=['is_val'])
        logger.info(f"训练集: {len(train_data)} 行, 验证集: {len(val_data)} 行")
    elif panel_df is not None:
        logger.info("从原始面板数据计算特征...")
        df = _normalize_input_df(panel_df)

        df['日期'] = pd.to_datetime(df['日期'])

        # 只保留最近3年数据，避免内存溢出
        last_date = df['日期'].max()
        cutoff_date = last_date - pd.DateOffset(years=3)
        before_cut = len(df)
        df = df[df['日期'] >= cutoff_date].copy()
        after_cut = len(df)
        logger.info(f"数据截取（最近3年）: {before_cut} 行 -> {after_cut} 行 "
                     f"(范围: {df['日期'].min()} 到 {df['日期'].max()})")

        df = df.sort_values(['日期', '股票代码']).reset_index(drop=True)
        val_start = (last_date - pd.DateOffset(months=2)).normalize()
        train_raw = df[df['日期'] < val_start].copy()
        val_raw = df[df['日期'] >= val_start].copy()
        train_raw['日期'] = train_raw['日期'].dt.strftime('%Y-%m-%d')
        val_raw['日期'] = val_raw['日期'].dt.strftime('%Y-%m-%d')
        val_start_date = val_start.strftime('%Y-%m-%d')

        logger.info(f"训练集范围: {train_raw['日期'].min()} 到 {train_raw['日期'].max()}")
        logger.info(f"验证集范围: {val_raw['日期'].min()} 到 {val_raw['日期'].max()}")

        all_stock_ids = sorted(df['股票代码'].unique())
        stockid2idx = {sid: idx for idx, sid in enumerate(all_stock_ids)}
        num_stocks = len(stockid2idx)

        train_data, features = preprocess_data(train_raw, is_train=True, stockid2idx=stockid2idx, config=config)
        val_data, _ = preprocess_val_data(val_raw, stockid2idx=stockid2idx, config=config)

        # 标准化
        import joblib
        scaler = StandardScaler()
        train_data[features] = train_data[features].replace([np.inf, -np.inf], np.nan)
        val_data[features] = val_data[features].replace([np.inf, -np.inf], np.nan)
        train_data = train_data.dropna(subset=features)
        val_data = val_data.dropna(subset=features)
        train_data[features] = scaler.fit_transform(train_data[features])
        val_data[features] = scaler.transform(val_data[features])
        joblib.dump(scaler, os.path.join(output_dir, 'scaler.pkl'))
    else:
        logger.error("请提供 panel_df 或有效的 feature_path")
        return None

    # 探针法特征筛选：支持在线计算和预计算特征两种训练路径。
    if config.get('use_probe_selection', False):
        from data_processor.probe_selection import load_selected_features, probe_feature_selection

        probe_output_path = os.path.join(output_dir, 'probe_selection.json')
        selected_path = config.get('probe_selected_features_path') or probe_output_path
        if selected_path and os.path.exists(selected_path):
            features = load_selected_features(selected_path)
            logger.info(f"加载已保存的探针法特征筛选结果: {selected_path}，保留 {len(features)} 个特征")
        else:
            logger.info("开始探针法特征筛选...")
            features, _ = probe_feature_selection(
                train_data, features,
                n_iter=config.get('probe_n_iter', 10),
                n_noise=config.get('probe_n_noise', 10),
                output_path=probe_output_path,
            )
        train_data = train_data.dropna(subset=features)
        val_data = val_data.dropna(subset=features)

    # 创建数据集
    train_result = create_ranking_dataset_vectorized(
        train_data, features, config['sequence_length']
    )
    val_source = pd.concat([train_data, val_data], ignore_index=True)
    val_result = create_ranking_dataset_vectorized(
        val_source, features, config['sequence_length'],
        min_window_end_date=val_start_date,
    )

    train_sequences, train_targets, train_relevance, train_stock_indices = train_result[:4]
    val_sequences, val_targets, val_relevance, val_stock_indices = val_result[:4]
    train_cls = train_result[4] if len(train_result) > 4 else None
    train_dir = train_result[5] if len(train_result) > 5 else None
    val_cls = val_result[4] if len(val_result) > 4 else None
    val_dir = val_result[5] if len(val_result) > 5 else None

    logger.info(f"训练集样本数: {len(train_sequences)}, 特征数: {len(features)}")
    logger.info(f"验证集样本数: {len(val_sequences)}")

    train_dataset = RankingDataset(
        train_sequences, train_targets, train_relevance, train_stock_indices,
        cls_labels=train_cls, dir_labels=train_dir
    )
    val_dataset = RankingDataset(
        val_sequences, val_targets, val_relevance, val_stock_indices,
        cls_labels=val_cls, dir_labels=val_dir
    )

    train_loader = DataLoader(train_dataset, batch_size=config['batch_size'], shuffle=True,
                              collate_fn=collate_fn, num_workers=0, pin_memory=False)
    val_loader = DataLoader(val_dataset, batch_size=config['batch_size'], shuffle=False,
                            collate_fn=collate_fn, num_workers=0, pin_memory=False)

    # 模型初始化
    if config.get('use_multi_head', False):
        model = MultiHeadStockTransformer(input_dim=len(features), config=config, num_stocks=num_stocks)
        logger.info("使用多头 Transformer 模型")
    else:
        model = StockTransformer(input_dim=len(features), config=config, num_stocks=num_stocks)
    model.to(device)
    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    logger.info(f"模型参数量: {total_params:,}")

    # 损失函数和优化器
    base_criterion = WeightedRankingLoss(
        k=5, temperature=1.0, weight_factor=config['top5_weight'],
        pairwise_weight=config['pairwise_weight'], base_weight=config.get('base_weight', 1.0)
    )
    if config.get('use_multi_head', False):
        criterion = MultiTaskRankingLoss(
            ranking_loss=base_criterion,
            reg_weight=config.get('reg_weight', 0.1),
            cls_weight=config.get('cls_weight', 0.1),
            dir_weight=config.get('dir_weight', 0.1)
        )
        logger.info("使用多任务损失函数")
    else:
        criterion = base_criterion

    optimizer = torch.optim.AdamW(model.parameters(), lr=config['learning_rate'], weight_decay=1e-5)
    scheduler = torch.optim.lr_scheduler.LinearLR(
        optimizer, start_factor=1.0, end_factor=0.2, total_iters=config['num_epochs']
    )

    # 训练循环
    best_score = -float('inf')
    best_epoch = -1
    history = []  # 记录每个epoch的指标

    for epoch in range(config['num_epochs']):
        logger.info(f"\n=== Epoch {epoch+1}/{config['num_epochs']} ===")

        train_loss, train_metrics = train_ranking_model(
            model, train_loader, criterion, optimizer, device, epoch, config
        )
        logger.info(f"Train Loss: {train_loss:.4f}")
        for k, v in train_metrics.items():
            logger.info(f"Train {k}: {v:.4f}")

        eval_loss, eval_metrics = evaluate_ranking_model(
            model, val_loader, criterion, device, config, epoch=epoch
        )
        logger.info(f"Eval Loss: {eval_loss:.4f}")
        for k, v in eval_metrics.items():
            logger.info(f"Eval {k}: {v:.4f}")

        scheduler.step()

        # 记录历史
        epoch_history = {
            'train_loss': train_loss,
            'eval_loss': eval_loss,
            'train_final_score': train_metrics.get('final_score', 0.0),
            'eval_final_score': eval_metrics.get('final_score', 0.0),
            'final_score': eval_metrics.get('final_score', train_metrics.get('final_score', 0.0)),
            'train_pred_return_sum': train_metrics.get('pred_return_sum', 0.0),
            'eval_pred_return_sum': eval_metrics.get('pred_return_sum', 0.0),
            'pred_return_sum': eval_metrics.get('pred_return_sum', train_metrics.get('pred_return_sum', 0.0)),
            'train_max_return_sum': train_metrics.get('max_return_sum', 0.0),
            'eval_max_return_sum': eval_metrics.get('max_return_sum', 0.0),
            'max_return_sum': eval_metrics.get('max_return_sum', train_metrics.get('max_return_sum', 0.0)),
            'train_random_return_sum': train_metrics.get('random_return_sum', 0.0),
            'eval_random_return_sum': eval_metrics.get('random_return_sum', 0.0),
            'random_return_sum': eval_metrics.get('random_return_sum', train_metrics.get('random_return_sum', 0.0)),
            'train_ratio_pred': train_metrics.get('ratio_pred', 0.0),
            'eval_ratio_pred': eval_metrics.get('ratio_pred', 0.0),
            'ratio_pred': eval_metrics.get('ratio_pred', train_metrics.get('ratio_pred', 0.0)),
        }
        history.append(epoch_history)

        current_final_score = eval_metrics.get('final_score', 0.0)
        if current_final_score > best_score:
            best_score = current_final_score
            best_epoch = epoch + 1
            model_name = save_name or 'best_model'
            torch.save(model.state_dict(), os.path.join(output_dir, f'{model_name}.pth'))
            logger.info(f"保存最佳模型 - final score: {best_score:.4f}")

    logger.info(f"\n训练完成！最佳 epoch: {best_epoch}, 最佳 final score: {best_score:.4f}")

    with open(os.path.join(output_dir, 'final_score.txt'), 'w') as f:
        f.write(f"Best epoch: {best_epoch}\nBest final_score: {best_score:.6f}\n")

    # 保存训练历史
    with open(os.path.join(output_dir, 'training_history.json'), 'w') as f:
        json.dump(history, f, indent=4, ensure_ascii=False)

    # 绘制训练历史图表
    try:
        from visualization.plotter import StockPlotter
        plotter = StockPlotter()
        plot_dir = os.path.join(output_dir, 'plots')
        os.makedirs(plot_dir, exist_ok=True)
        history_save_path = os.path.join(plot_dir, 'training_history.png')
        plotter.plot_transformer_training_history(history, title='Transformer 训练历史', save_path=history_save_path)
        logger.info(f"训练历史图表已保存至: {history_save_path}")
    except Exception as e:
        logger.warning(f"训练历史图表绘制失败: {e}")

    return {
        'model_path': os.path.join(output_dir, f'{save_name or "best_model"}.pth'),
        'scaler_path': os.path.join(output_dir, 'scaler.pkl'),
        'feature_names': features,
        'stockid2idx': stockid2idx,
        'best_score': best_score,
        'best_epoch': best_epoch,
        'history': history,
        'config': config,
    }


# ============================================================
# 预测函数
# ============================================================

def predict_top_stocks_transformer(panel_df=None, feature_path=None, model_path=None,
                                     scaler_path=None, config=None, top_k=5):
    """
    使用训练好的 Transformer 模型进行预测。

    参数:
        panel_df: 原始面板数据（提供此参数时会重新计算特征）
        feature_path: 预计算特征文件路径（提供此参数时跳过特征计算）
        model_path: 模型路径
        scaler_path: Scaler路径
        config: 配置
        top_k: 返回Top K股票

    返回:
        DataFrame: 排名结果
    """
    if config is None:
        config = TRANSFORMER_CONFIG.copy()

    if model_path is None:
        output_dir = config['output_dir']
        files = [f for f in os.listdir(output_dir) if f.endswith('.pth')]
        if not files:
            logger.error("无可用模型文件")
            return None
        model_path = os.path.join(output_dir, files[0])

    feature_num = config['feature_num']
    import joblib

    # --- 特征准备 ---
    already_standardized = False
    if feature_path and os.path.exists(feature_path):
        logger.info(f"从预计算特征加载: {feature_path}")
        processed, full_features, stockid2idx, _ = load_precomputed_features(feature_path, config)
        # parquet 中的数据已经标准化过，后续不再需要 scaler
        already_standardized = True
        num_stocks = len(stockid2idx)

        # 使用探针法筛选特征（如果有）
        features = full_features
        probe_path = os.path.join(os.path.dirname(model_path), 'probe_selection.json')
        if os.path.exists(probe_path):
            with open(probe_path, 'r', encoding='utf-8') as f:
                probe_data = json.load(f)
            features = probe_data['selected_features']
            logger.info(f"使用探针法筛选特征: {len(features)} 个")

        # 不在此处过滤日期——预测需要最近 sequence_length 天的完整序列
        scaler_path = scaler_path or feature_path.replace('.parquet', '_scaler.pkl')

        # 清理 is_val 列（parquet 中可能残留）
        if 'is_val' in processed.columns:
            processed = processed.drop(columns=['is_val'])
    elif panel_df is not None:
        logger.info("从原始面板数据计算特征...")
        df = _normalize_input_df(panel_df)

        df['日期'] = pd.to_datetime(df['日期'])
        # 只保留最近3年数据
        last_date = df['日期'].max()
        cutoff_date = last_date - pd.DateOffset(years=3)
        before_cut = len(df)
        df = df[df['日期'] >= cutoff_date].copy()
        logger.info(f"数据截取（最近3年）: {before_cut} 行 -> {len(df)} 行")

        # 加载特征列表
        probe_path = os.path.join(os.path.dirname(model_path), 'probe_selection.json')
        if os.path.exists(probe_path):
            with open(probe_path, 'r') as f:
                probe_data = json.load(f)
            features = probe_data['selected_features']
        else:
            features = FEATURE_COLUMNS_MAP[feature_num]
            features = [f for f in features if f not in ('instrument', '股票代码', '日期', 'label')]

        all_stock_ids = sorted(df['股票代码'].unique())
        stockid2idx = {sid: idx for idx, sid in enumerate(all_stock_ids)}
        num_stocks = len(stockid2idx)

        processed, _ = preprocess_data(df, is_train=False, stockid2idx=stockid2idx, config=config)
        processed[features] = processed[features].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        scaler_path = scaler_path or os.path.join(os.path.dirname(model_path), 'scaler.pkl')
    else:
        logger.error("请提供 panel_df 或有效的 feature_path")
        return None

    # 设置设备
    if torch.cuda.is_available():
        device = torch.device('cuda')
    elif torch.backends.mps.is_available():
        device = torch.device('mps')
    else:
        device = torch.device('cpu')

    # 加载 scaler 并标准化（如果数据已经标准化则跳过）
    if not already_standardized:
        scaler = joblib.load(scaler_path)
        # scaler 是用 full_features 训练的，先用全量特征标准化，再取子集
        scaler_feature_names = list(scaler.feature_names_in_) if hasattr(scaler, 'feature_names_in_') else None
        if scaler_feature_names and scaler_feature_names != features:
            processed[scaler_feature_names] = scaler.transform(processed[scaler_feature_names])
        else:
            processed[features] = scaler.transform(processed[features])

    # 构建预测序列
    latest_date = pd.to_datetime(processed['日期']).max()
    sequences = []
    stock_codes = []

    for stock_code in processed['股票代码'].unique():
        stock_history = processed[
            (processed['股票代码'] == stock_code) &
            (pd.to_datetime(processed['日期']) <= latest_date)
        ].sort_values('日期').tail(config['sequence_length'])

        if len(stock_history) == config['sequence_length']:
            sequences.append(stock_history[features].values.astype(np.float32))
            stock_codes.append(stock_code)

    if len(sequences) == 0:
        logger.error("没有可用于预测的股票序列")
        return None

    x = torch.FloatTensor(np.array(sequences)).unsqueeze(0).to(device)

    # 加载模型
    if config.get('use_multi_head', False):
        model = MultiHeadStockTransformer(input_dim=len(features), config=config, num_stocks=num_stocks)
    else:
        model = StockTransformer(input_dim=len(features), config=config, num_stocks=num_stocks)
    model.load_state_dict(torch.load(model_path, map_location=device, weights_only=True))
    model.to(device)
    model.eval()

    with torch.no_grad():
        if config.get('use_multi_head', False):
            outputs = model(x, return_all_heads=True)
            ranking_scores = outputs['ranking'].squeeze().cpu().numpy()
            reg_scores = outputs['regression'].squeeze().cpu().numpy()
            cls_scores = outputs['classification'].squeeze().cpu().numpy()
            dir_scores = outputs['direction'].squeeze().cpu().numpy()

            # 计算不确定性
            def minmax_norm(arr):
                xmin, xmax = arr.min(), arr.max()
                if xmax - xmin < 1e-9:
                    return np.zeros_like(arr)
                return (arr - xmin) / (xmax - xmin)

            reg_n = minmax_norm(reg_scores)
            cls_n = minmax_norm(cls_scores)
            dir_n = minmax_norm(dir_scores)
            uncertainty = np.var(np.stack([reg_n, cls_n, dir_n]), axis=0)
            adjusted_scores = ranking_scores * (1 - uncertainty)
            top_indices = np.argsort(adjusted_scores)[::-1][:top_k]
        else:
            scores = model(x).squeeze().cpu().numpy()
            top_indices = np.argsort(scores)[::-1][:top_k]
            ranking_scores = scores
            adjusted_scores = scores
            uncertainty = np.zeros_like(scores)

    results = []
    for rank, idx in enumerate(top_indices):
        results.append({
            '排名': rank + 1,
            '股票代码': stock_codes[idx],
            '预测分数': float(ranking_scores[idx]),
            '调整后分数': float(adjusted_scores[idx]),
            '不确定性': float(uncertainty[idx]),
        })

    result_df = pd.DataFrame(results)
    logger.info(f"\nTransformer 预测 Top {top_k}:")
    for _, row in result_df.iterrows():
        logger.info(f"  #{int(row['排名'])}  {row['股票代码']}  预测分数: {row['预测分数']:.6f}")

    # 计算权重（用于可视化）
    if config.get('use_multi_head', False):
        scores_for_weight = result_df['调整后分数'].values
    else:
        scores_for_weight = result_df['预测分数'].values

    # 使用 softmax 计算权重
    temp = config.get('weight_temperature', 0.5)
    exp_scores = np.exp(scores_for_weight / temp)
    weights = exp_scores / (exp_scores.sum() + 1e-12)
    result_df['权重'] = weights

    # 绘制可视化图表
    try:
        from visualization.plotter import StockPlotter
        plotter = StockPlotter()

        # 确定图表保存目录
        if model_path:
            plot_dir = os.path.join(os.path.dirname(model_path), 'plots')
        else:
            plot_dir = os.path.join(config['output_dir'], 'plots')
        os.makedirs(plot_dir, exist_ok=True)

        # Top N 排名柱状图
        ranking_plot_path = os.path.join(plot_dir, 'prediction_ranking.png')
        plotter.plot_top_n_ranking(result_df, title='Transformer 股票排名', save_path=ranking_plot_path)
        logger.info(f"排名图表已保存至: {ranking_plot_path}")

        # 不确定性分布
        uncertainty_plot_path = os.path.join(plot_dir, 'uncertainty_distribution.png')
        plotter.plot_uncertainty_distribution(result_df, title='模型不确定性分布', save_path=uncertainty_plot_path)
        logger.info(f"不确定性图表已保存至: {uncertainty_plot_path}")

        # 权重分配
        weight_plot_path = os.path.join(plot_dir, 'weight_allocation.png')
        plotter.plot_weight_allocation(result_df, title='动态权重分配', save_path=weight_plot_path)
        logger.info(f"权重图表已保存至: {weight_plot_path}")
    except Exception as e:
        logger.warning(f"预测可视化图表绘制失败: {e}")

    return result_df
