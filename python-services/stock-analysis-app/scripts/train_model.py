# -*- coding: utf-8 -*-
"""
独立模型训练脚本

从 Dashboard 中解耦出来的模型训练入口。
所有重计算操作都在这里执行，Dashboard 只负责展示已训练好的模型结果。

用法：
    # 训练 XGBoost 分类模型
    python scripts/train_model.py --model xgboost

    # 训练排名模型 (LightGBM)
    python scripts/train_model.py --model ranking

    # 训练 Transformer 模型
    python scripts/train_model.py --model transformer

    # 指定股票范围
    python scripts/train_model.py --model xgboost --stock-codes 000001.SZ 600519.SH
"""

import os
import sys
import argparse
from datetime import datetime

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import get_logger
from config.settings import MODEL_CONFIG, RANDOM_SEED
from analysis.model_registry import set_global_seed

logger = get_logger(__name__)


def train_xgboost_model(args):
    """训练 XGBoost 分类/回归模型。"""
    from data_processor.panel_builder import (
        load_all_stock_data_from_db, prepare_panel_for_training,
    )
    from analysis.predictor import StockPredictor
    from analysis.pipeline import build_training_samples

    set_global_seed(RANDOM_SEED)

    logger.info("=" * 60)
    logger.info("XGBoost 模型训练开始")
    logger.info("=" * 60)

    # 1. 加载数据
    panel_df = load_all_stock_data_from_db(stock_codes=args.stock_codes)
    if panel_df.empty:
        logger.error("数据加载失败")
        return

    # 2. 计算特征
    featured_df = prepare_panel_for_training(panel_df, forward_days=5)
    if featured_df.empty:
        logger.error("特征计算失败")
        return

    # 3. 构建训练样本
    predictor = StockPredictor(model_type=args.model)
    feature_result = predictor.prepare_features(
        featured_df, target_column='future_direction_1d'
    )
    if feature_result[0] is None:
        logger.error("特征准备失败")
        return

    X, y, feature_names = feature_result

    # 4. 训练
    result = predictor.train_xgboost(X, y, feature_names=feature_names)
    if result is None:
        logger.error("训练失败")
        return

    # 5. 保存模型
    model_path = predictor.save_model(
        feature_names=feature_names,
        train_start_date=featured_df['trade_date'].min(),
        train_end_date=featured_df['trade_date'].max(),
        metrics=result['metrics'],
    )

    logger.info("=" * 60)
    logger.info("训练完成: %s", model_path)
    logger.info("评价指标: %s", result['metrics'])
    logger.info("=" * 60)


def train_ranking_model(args):
    """训练 LightGBM 排名模型。"""
    from data_processor.panel_builder import (
        load_all_stock_data_from_db, prepare_panel_for_training,
    )
    from analysis.ranking_predictor import train_ranking_model as _train

    set_global_seed(RANDOM_SEED)

    logger.info("=" * 60)
    logger.info("排名模型训练开始")
    logger.info("=" * 60)

    panel_df = load_all_stock_data_from_db(stock_codes=args.stock_codes)
    if panel_df.empty:
        logger.error("数据加载失败")
        return

    featured_df = prepare_panel_for_training(panel_df, forward_days=5)
    if featured_df.empty:
        logger.error("特征计算失败")
        return

    result = _train(featured_df, use_probe=True)
    if result is None:
        logger.error("训练失败")
        return

    logger.info("=" * 60)
    logger.info("训练完成: %s", result.get('model_path', 'unknown'))
    logger.info("评价指标: %s", result.get('metrics', {}))
    logger.info("=" * 60)


def train_transformer_model(args):
    """训练 Transformer 排名模型。"""
    from data_processor.panel_builder import load_all_stock_data_from_db
    from analysis.transformer_trainer import run_transformer_training

    set_global_seed(RANDOM_SEED)

    logger.info("=" * 60)
    logger.info("Transformer 模型训练开始")
    logger.info("=" * 60)

    panel_df = load_all_stock_data_from_db(stock_codes=args.stock_codes)
    if panel_df.empty:
        logger.error("数据加载失败")
        return

    run_transformer_training(panel_df=panel_df)

    logger.info("=" * 60)
    logger.info("Transformer 训练完成")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(description='独立模型训练脚本')
    parser.add_argument(
        '--model', '-m',
        choices=['xgboost', 'xgboost_regression', 'ranking', 'transformer'],
        default='xgboost',
        help='模型类型 (default: xgboost)',
    )
    parser.add_argument(
        '--stock-codes', nargs='+', default=None,
        help='指定股票代码列表 (空格分隔)，不指定则使用全市场',
    )
    parser.add_argument(
        '--seed', type=int, default=RANDOM_SEED,
        help=f'随机种子 (default: {RANDOM_SEED})',
    )
    args = parser.parse_args()

    logger.info("训练参数: model=%s, seed=%d, stocks=%s",
                args.model, args.seed, args.stock_codes or '全市场')

    if args.model in ('xgboost', 'xgboost_regression'):
        train_xgboost_model(args)
    elif args.model == 'ranking':
        train_ranking_model(args)
    elif args.model == 'transformer':
        train_transformer_model(args)
    else:
        logger.error("不支持的模型类型: %s", args.model)


if __name__ == '__main__':
    main()
