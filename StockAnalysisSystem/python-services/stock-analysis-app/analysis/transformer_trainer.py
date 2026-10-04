# -*- coding: utf-8 -*-
"""
Transformer 训练和预测入口

基于 app/code/src/train.py 和 train.py 的训练逻辑整合，
适配 StockAnalysisSystem 的调用方式。
"""
import os
import json
import random
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset
from tqdm import tqdm
from config.logging_config import get_logger
logger = get_logger(__name__)
from analysis.transformer_config import TRANSFORMER_CONFIG
from analysis.transformer_model import StockTransformer, MultiHeadStockTransformer
from analysis.training_control import EarlyStopping
from analysis.transformer_features import (
    build_feature_panel, normalize_panel, prepare_training_data, prepare_inference_data,
    save_model_preprocessing, load_model_preprocessing,
)
from analysis.transformer_utils import (
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


def preprocess_data(df, is_train=True, stockid2idx=None, config=None):
    """Raw features; inference never requires future prices or labels."""
    frame, features, _, _ = build_feature_panel(
        df, config or TRANSFORMER_CONFIG, stockid2idx, include_labels=is_train)
    if is_train:
        frame = frame[np.isfinite(frame['label'])].copy()
    return frame, features


def preprocess_val_data(df, stockid2idx=None, config=None):
    return preprocess_data(df, True, stockid2idx, config)


def _normalize_input_df(df):
    return normalize_panel(df)


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

    # Both entrances supply raw units to one purged split and fitted scaler.
    if feature_path:
        all_data, features, stockid2idx, val_start_date = load_precomputed_features(feature_path, config)
    elif panel_df is not None:
        all_data, features, stockid2idx, val_start_date = build_feature_panel(panel_df, config)
    else:
        raise ValueError("Provide panel_df or feature_path")
    full_features = list(features)
    history_start = all_data['日期'].min()
    stock_history_starts = all_data.groupby('股票代码')['日期'].min().to_dict()
    num_stocks = len(stockid2idx)
    train_data, val_data, val_source, scaler = prepare_training_data(all_data, features, val_start_date)

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
    train_source = val_source[val_source['日期'] < pd.Timestamp(val_start_date)].copy()
    train_result = create_ranking_dataset_vectorized(
        train_source, features, config['sequence_length']
    )
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
    stopping = EarlyStopping(config.get('early_stopping_patience', 0),
                             config.get('early_stopping_min_delta', 0.))
    stopped_early = False
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
        improved, should_stop = stopping.update(current_final_score)
        if improved:
            best_score = current_final_score
            best_epoch = epoch + 1
            model_name = save_name or 'best_model'
            torch.save(model.state_dict(), os.path.join(output_dir, f'{model_name}.pth'))
            logger.info(f"保存最佳模型 - final score: {best_score:.4f}")
        if should_stop:
            stopped_early = True
            logger.info(f"验证指标连续 {stopping.patience} 轮未改善，提前停止")
            break

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

    model_path = os.path.join(output_dir, f'{save_name or "best_model"}.pth')
    if best_epoch < 0:
        raise ValueError('No valid checkpoint produced')
    scaler_path = save_model_preprocessing(model_path, scaler, full_features, features,
                                           stockid2idx, config, history_start, stock_history_starts)
    return {
        'model_path': os.path.join(output_dir, f'{save_name or "best_model"}.pth'),
        'scaler_path': scaler_path,
        'feature_names': features,
        'stockid2idx': stockid2idx,
        'best_score': best_score,
        'best_epoch': best_epoch,
        'epochs_completed': len(history),
        'stopped_early': stopped_early,
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

    config = {**TRANSFORMER_CONFIG, **config}
    scaler, manifest = load_model_preprocessing(model_path, config, scaler_path)
    full_features = manifest['full_features']
    features = manifest['selected_features']
    stockid2idx = manifest['stockid2idx']
    num_stocks = len(stockid2idx)
    inference_config = dict(config, feature_start_date=manifest['feature_history_start'],
                            stock_history_starts=manifest['stock_history_starts'])
    if feature_path:
        # Validate disposable cache format, then rebuild with the model's origin.
        load_precomputed_features(feature_path, config)
        raw_path = os.path.join(os.path.dirname(feature_path), 'raw_panel.parquet')
        if not os.path.exists(raw_path):
            raise ValueError("Raw history cache missing; rebuild before inference")
        panel_df = pd.read_parquet(raw_path, engine='pyarrow')
    if panel_df is None:
        raise ValueError("Provide panel_df or feature_path")
    normalized = normalize_panel(panel_df)
    latest_date = normalized['日期'].max()
    known = normalized[normalized['股票代码'].isin(stockid2idx)]
    if known.empty:
        raise ValueError("No stocks known to model")
    for code, first_date in known.groupby('股票代码')['日期'].min().items():
        if first_date > pd.Timestamp(manifest['stock_history_starts'][code]):
            raise ValueError(f"Incomplete model feature history for {code}; reload raw data or retrain")
    processed, computed, _, _ = build_feature_panel(
        normalized, inference_config, stockid2idx, include_labels=False)
    if computed != full_features:
        raise ValueError("Model feature configuration mismatch")
    processed = prepare_inference_data(processed, full_features, scaler)

    # 设置设备
    if torch.cuda.is_available():
        device = torch.device('cuda')
    elif torch.backends.mps.is_available():
        device = torch.device('mps')
    else:
        device = torch.device('cpu')


    # 构建预测序列
    sequences = []
    stock_codes = []

    for stock_code in processed['股票代码'].unique():
        stock_history = processed[
            (processed['股票代码'] == stock_code) &
            (pd.to_datetime(processed['日期']) <= latest_date)
        ].sort_values('日期').tail(config['sequence_length'])

        if len(stock_history) == config['sequence_length'] and stock_history['日期'].max() == latest_date:
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
            ranking_scores = outputs['ranking'].reshape(-1).cpu().numpy()
            reg_scores = outputs['regression'].reshape(-1).cpu().numpy()
            cls_scores = outputs['classification'].reshape(-1).cpu().numpy()
            dir_scores = outputs['direction'].reshape(-1).cpu().numpy()

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
            scores = model(x).reshape(-1).cpu().numpy()
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
