# -*- coding: utf-8 -*-
"""
基线数据构建脚本

选择固定范围（20 只股票，最近 1 年），计算并保存基准结果。
后续 Kafka、Flink、Spark 改造后，都用这批数据对账。

用法：
    python scripts/build_baseline.py

输出：
    models/baseline/baseline_report.json  ── 基准结果报告
    models/baseline/baseline_panel.parquet ── 基线面板数据
"""

import os
import sys
import json
import hashlib
import numpy as np
import pandas as pd
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.logging_config import get_logger
from config.settings import RANDOM_SEED
from analysis.model_registry import set_global_seed

logger = get_logger(__name__)

# 基线股票池（20 只，覆盖不同行业、不同市值）
BASELINE_STOCKS = [
    '000001.SZ',  # 平安银行
    '000002.SZ',  # 万科A
    '000858.SZ',  # 五粮液
    '002594.SZ',  # 比亚迪
    '600000.SH',  # 浦发银行
    '600009.SH',  # 上海机场
    '600016.SH',  # 民生银行
    '600028.SH',  # 中国石化
    '600030.SH',  # 中信证券
    '600036.SH',  # 招商银行
    '600050.SH',  # 中国联通
    '600104.SH',  # 上汽集团
    '600276.SH',  # 恒瑞医药
    '600519.SH',  # 贵州茅台
    '600585.SH',  # 海螺水泥
    '600887.SH',  # 伊利股份
    '601006.SH',  # 大秦铁路
    '601318.SH',  # 中国平安
    '601398.SH',  # 工商银行
    '603259.SH',  # 药明康德
]

BASELINE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            'models', 'baseline')


def build_baseline():
    """构建并保存基线数据。"""
    set_global_seed(RANDOM_SEED)
    os.makedirs(BASELINE_DIR, exist_ok=True)

    logger.info("=" * 60)
    logger.info("基线数据构建开始")
    logger.info("股票池: %d 只 | 时间范围: 最近 1 年", len(BASELINE_STOCKS))
    logger.info("=" * 60)

    # 1. 加载数据
    from data_processor.panel_builder import load_all_stock_data_from_db
    end_date = datetime.now().strftime('%Y%m%d')
    start_date = (datetime.now() - timedelta(days=365)).strftime('%Y%m%d')

    panel_df = load_all_stock_data_from_db(
        stock_codes=BASELINE_STOCKS,
        start_date=start_date,
        end_date=end_date,
    )

    if panel_df.empty:
        logger.error("数据加载失败，请检查数据库")
        return None

    # 2. 统计原始数据
    raw_rows = len(panel_df)
    raw_stocks = panel_df['ts_code'].nunique()
    duplicates = panel_df.duplicated(subset=['ts_code', 'trade_date']).sum()

    logger.info("原始数据: %d 行, %d 只股票, %d 条重复", raw_rows, raw_stocks, duplicates)

    # 3. 去重 + 清洗
    panel_df = panel_df.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
    panel_df['trade_date'] = pd.to_datetime(panel_df['trade_date'])
    cleaned_rows = len(panel_df)

    # 4. 计算特征
    from data_processor.feature_engineer import FeatureEngineer
    engineer = FeatureEngineer()
    results = []

    for code in BASELINE_STOCKS:
        stock_df = panel_df[panel_df['ts_code'] == code].copy()
        stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)
        if len(stock_df) < 60:
            logger.warning("股票 %s 数据不足 60 行，跳过", code)
            continue
        try:
            featured = engineer.calculate_all_features(stock_df)
            results.append(featured)
        except Exception as e:
            logger.warning("股票 %s 特征计算失败: %s", code, e)

    if not results:
        logger.error("无股票成功计算特征")
        return None

    featured_panel = pd.concat(results, ignore_index=True)
    featured_panel = featured_panel.replace([np.inf, -np.inf], np.nan)

    # 5. 提取关键指标快照（取最后交易日的数据）
    last_date = featured_panel['trade_date'].max()
    snapshot = featured_panel[featured_panel['trade_date'] == last_date].copy()

    key_indicators = {}
    for code in snapshot['ts_code'].unique():
        row = snapshot[snapshot['ts_code'] == code].iloc[0]
        key_indicators[code] = {
            'ma5': _safe_float(row.get('ma5')),
            'ma20': _safe_float(row.get('ma20')),
            'rsi': _safe_float(row.get('rsi')),
            'macd_dif': _safe_float(row.get('macd_dif')),
            'macd_dea': _safe_float(row.get('macd_dea')),
            'close': _safe_float(row.get('close')),
            'volatility_20d': _safe_float(row.get('volatility_20d')),
        }

    # 6. 保存基线面板
    panel_path = os.path.join(BASELINE_DIR, 'baseline_panel.parquet')
    featured_panel.to_parquet(panel_path, engine='pyarrow', index=False)
    logger.info("基线面板已保存: %s (%d 行)", panel_path, len(featured_panel))

    # 7. 构建报告
    report = {
        'version': '1.0.0',
        'created_at': datetime.now().isoformat(timespec='seconds'),
        'random_seed': RANDOM_SEED,
        'stock_pool': BASELINE_STOCKS,
        'n_stocks': raw_stocks,
        'date_range': {
            'start': start_date,
            'end': end_date,
        },
        'data_stats': {
            'raw_rows': raw_rows,
            'cleaned_rows': cleaned_rows,
            'duplicates': int(duplicates),
            'stocks_with_features': len(results),
        },
        'key_indicators_snapshot': key_indicators,
        'panel_hash': _file_hash(panel_path),
    }

    # 8. 尝试训练一个基线模型并记录 Top 10 + 评价指标
    try:
        model_metrics = _train_baseline_model(featured_panel)
        report['baseline_model'] = model_metrics
    except Exception as e:
        logger.warning("基线模型训练失败: %s", e)
        report['baseline_model'] = {'error': str(e)}

    # 9. 保存报告
    report_path = os.path.join(BASELINE_DIR, 'baseline_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    logger.info("=" * 60)
    logger.info("基线报告已保存: %s", report_path)
    logger.info("  原始行数: %d", raw_rows)
    logger.info("  清洗后行数: %d", cleaned_rows)
    logger.info("  重复数据: %d", duplicates)
    logger.info("=" * 60)

    return report


def _train_baseline_model(featured_panel):
    """训练基线 XGBoost 模型并返回评价指标 + Top 10。"""
    from analysis.predictor import StockPredictor

    predictor = StockPredictor(model_type='xgboost')
    feature_result = predictor.prepare_features(
        featured_panel, target_column='future_direction_1d'
    )
    if feature_result[0] is None:
        return {'error': '特征准备失败'}

    X, y, feature_names = feature_result
    result = predictor.train_xgboost(X, y, feature_names=feature_names)
    if result is None:
        return {'error': '训练失败'}

    # Top 10 预测（用最后一天的数据）
    latest_X = X[-20:]  # 最后 20 行
    latest_y = y[-20:]
    try:
        predictions = predictor.model.predict_proba(latest_X)[:, 1]
        top_10_idx = np.argsort(predictions)[-10:][::-1]
        top_10 = [
            {'index': int(i), 'score': float(predictions[i])}
            for i in top_10_idx
        ]
    except Exception:
        top_10 = []

    return {
        'metrics': {k: float(v) for k, v in result['metrics'].items()
                    if isinstance(v, (int, float, np.floating))},
        'top_10': top_10,
        'n_features': len(feature_names),
    }


def _safe_float(val):
    """安全转换为 float，NaN/None 返回 None。"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    try:
        return round(float(val), 6)
    except (TypeError, ValueError):
        return None


def _file_hash(path):
    """计算文件的 MD5 哈希。"""
    h = hashlib.md5()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()


if __name__ == '__main__':
    build_baseline()
