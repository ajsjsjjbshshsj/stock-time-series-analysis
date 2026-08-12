# -*- coding: utf-8 -*-
"""
Transformer 模型定义

基于 app/code/src/model.py 迁移，实现：
- StockTransformer：单头 Transformer + 跨股票注意力
- MultiHeadStockTransformer：多头多任务 Transformer
"""
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np


class PositionalEncoding(nn.Module):
    """位置编码模块"""
    def __init__(self, d_model, dropout=0.1, max_len=5000):
        super(PositionalEncoding, self).__init__()
        self.dropout = nn.Dropout(p=dropout)

        pe = torch.zeros(max_len, d_model)
        position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        pe = pe.unsqueeze(0)
        self.register_buffer('pe', pe)

    def forward(self, x):
        x = x + self.pe[:, :x.size(1)]
        return self.dropout(x)


class CrossStockAttention(nn.Module):
    """股票间交互注意力模块"""
    def __init__(self, d_model, nhead, dropout=0.1):
        super(CrossStockAttention, self).__init__()
        self.cross_attention = nn.MultiheadAttention(d_model, nhead, dropout=dropout, batch_first=True)
        self.norm = nn.LayerNorm(d_model)
        self.dropout = nn.Dropout(dropout)

    def forward(self, stock_features):
        # stock_features: [batch, num_stocks, d_model]
        attended, _ = self.cross_attention(stock_features, stock_features, stock_features)
        output = self.norm(stock_features + self.dropout(attended))
        return output


class FeatureAttention(nn.Module):
    """特征注意力模块"""
    def __init__(self, d_model, dropout=0.1):
        super(FeatureAttention, self).__init__()
        self.attention = nn.Sequential(
            nn.Linear(d_model, d_model // 2),
            nn.Tanh(),
            nn.Linear(d_model // 2, 1),
            nn.Softmax(dim=1)
        )
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        # x: [batch*num_stocks, seq_len, d_model]
        attention_weights = self.attention(x)  # [batch*num_stocks, seq_len, 1]
        attended = torch.sum(x * attention_weights, dim=1)  # [batch*num_stocks, d_model]
        return self.dropout(attended)


class StockTransformer(nn.Module):
    """
    单头 Transformer：用于股票排序。

    结构：输入投影 -> 位置编码 -> 时序Transformer编码器 -> 特征注意力
         -> 跨股票注意力 -> 排序层 -> 分数输出
    """
    def __init__(self, input_dim, config, num_stocks, emb_dim=16):
        super(StockTransformer, self).__init__()
        self.model_type = 'RankingTransformer'
        self.config = config
        self.num_stocks = num_stocks

        # 输入投影层
        self.input_proj = nn.Linear(input_dim, config['d_model'])
        self.pos_encoder = PositionalEncoding(config['d_model'], config['dropout'], config['sequence_length'])

        # 时序特征提取
        encoder_layer = nn.TransformerEncoderLayer(
            d_model=config['d_model'],
            nhead=config['nhead'],
            dim_feedforward=config['dim_feedforward'],
            dropout=config['dropout'],
            batch_first=True
        )
        self.temporal_encoder = nn.TransformerEncoder(encoder_layer, num_layers=config['num_layers'])

        # 特征注意力
        self.feature_attention = FeatureAttention(config['d_model'], config['dropout'])

        # 股票间交互注意力
        self.cross_stock_attention = CrossStockAttention(config['d_model'], config['nhead'], config['dropout'])

        # 排序特异性层
        self.ranking_layers = nn.Sequential(
            nn.Linear(config['d_model'], config['d_model']),
            nn.LayerNorm(config['d_model']),
            nn.ReLU(),
            nn.Dropout(config['dropout']),
            nn.Linear(config['d_model'], config['d_model'] // 2),
            nn.LayerNorm(config['d_model'] // 2),
            nn.ReLU(),
            nn.Dropout(config['dropout'])
        )

        # 最终排序分数输出
        self.score_head = nn.Sequential(
            nn.Linear(config['d_model'] // 2, config['d_model'] // 4),
            nn.ReLU(),
            nn.Dropout(config['dropout'] * 0.5),
            nn.Linear(config['d_model'] // 4, 1)
        )

        # 初始化权重
        self._init_weights()

    def _init_weights(self):
        """初始化模型权重"""
        for module in self.modules():
            if isinstance(module, nn.Linear):
                nn.init.xavier_uniform_(module.weight)
                if module.bias is not None:
                    nn.init.zeros_(module.bias)

    def forward(self, src):
        # src: [batch, num_stocks, seq_len, feature_dim]
        batch_size, num_stocks, seq_len, feature_dim = src.size()

        # 重塑为 [batch*num_stocks, seq_len, feature_dim]
        src_reshaped = src.view(batch_size * num_stocks, seq_len, feature_dim)

        # 输入投影和位置编码
        src_proj = self.input_proj(src_reshaped)  # [batch*num_stocks, seq_len, d_model]
        src_proj = self.pos_encoder(src_proj)

        # 时序特征提取
        temporal_features = self.temporal_encoder(src_proj)  # [batch*num_stocks, seq_len, d_model]

        # 特征注意力聚合
        aggregated_features = self.feature_attention(temporal_features)  # [batch*num_stocks, d_model]

        # 重塑回股票维度用于股票间交互
        stock_features = aggregated_features.view(batch_size, num_stocks, -1)  # [batch, num_stocks, d_model]

        # 股票间交互注意力
        interactive_features = self.cross_stock_attention(stock_features)  # [batch, num_stocks, d_model]

        # 重塑回原形状
        interactive_features = interactive_features.view(batch_size * num_stocks, -1)

        # 排序特异性变换
        ranking_features = self.ranking_layers(interactive_features)  # [batch*num_stocks, d_model//2]

        # 生成排序分数
        scores = self.score_head(ranking_features)  # [batch*num_stocks, 1]

        # 重塑为最终输出格式
        output = scores.view(batch_size, num_stocks)  # [batch, num_stocks]

        return output


class MultiHeadStockTransformer(StockTransformer):
    """
    多头 Transformer：共享编码器，分出 4 个预测头。

    对应探针法的"多模型不确定性"思想：
    - ranking 头：排序分数（listwise+pairwise 损失）
    - regression 头：预测收益率（MSE 损失）
    - classification 头：P(超过中位数)（BCE 损失）
    - direction 头：P(上涨)（BCE 损失）

    推理时通过 heads 之间的分歧程度度量"不确定性"。
    """
    def __init__(self, input_dim, config, num_stocks):
        super().__init__(input_dim, config, num_stocks)

        # Regression head: 预测原始收益率
        self.reg_head = nn.Sequential(
            nn.Linear(config['d_model'] // 2, config['d_model'] // 4),
            nn.ReLU(),
            nn.Dropout(config['dropout'] * 0.5),
            nn.Linear(config['d_model'] // 4, 1)
        )

        # Classification head: P(超过当日中位数)
        self.cls_head = nn.Sequential(
            nn.Linear(config['d_model'] // 2, config['d_model'] // 4),
            nn.ReLU(),
            nn.Dropout(config['dropout'] * 0.5),
            nn.Linear(config['d_model'] // 4, 1),
            nn.Sigmoid()
        )

        # Direction head: P(上涨)
        self.dir_head = nn.Sequential(
            nn.Linear(config['d_model'] // 2, config['d_model'] // 4),
            nn.ReLU(),
            nn.Dropout(config['dropout'] * 0.5),
            nn.Linear(config['d_model'] // 4, 1),
            nn.Sigmoid()
        )

    def forward(self, src, return_all_heads=False):
        # src: [batch, num_stocks, seq_len, feature_dim]
        batch_size, num_stocks, seq_len, feature_dim = src.size()

        # 共享编码器（与父类相同逻辑）
        src_reshaped = src.view(batch_size * num_stocks, seq_len, feature_dim)
        src_proj = self.input_proj(src_reshaped)
        src_proj = self.pos_encoder(src_proj)
        temporal_features = self.temporal_encoder(src_proj)
        aggregated_features = self.feature_attention(temporal_features)
        stock_features = aggregated_features.view(batch_size, num_stocks, -1)
        interactive_features = self.cross_stock_attention(stock_features)
        interactive_features = interactive_features.view(batch_size * num_stocks, -1)
        ranking_features = self.ranking_layers(interactive_features)

        # 主排序头
        scores = self.score_head(ranking_features)

        if not return_all_heads:
            # 默认行为：与父类完全相同
            return scores.view(batch_size, num_stocks)

        # 多头模式：返回 4 个头的输出
        reg_out = self.reg_head(ranking_features).view(batch_size, num_stocks)
        cls_out = self.cls_head(ranking_features).view(batch_size, num_stocks)
        dir_out = self.dir_head(ranking_features).view(batch_size, num_stocks)

        return {
            'ranking': scores.view(batch_size, num_stocks),
            'regression': reg_out,
            'classification': cls_out,
            'direction': dir_out
        }
