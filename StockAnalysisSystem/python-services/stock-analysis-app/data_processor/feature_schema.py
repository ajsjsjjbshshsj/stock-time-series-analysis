"""Canonical traditional feature-storage columns, shared by both writers.

Future labels are deliberately absent. This is not the Transformer model schema.
Keep the existing column names/order for database and legacy caller compatibility.
"""

FEATURE_COL_MAP = {
    # 均线
    'ma5': 'ma5', 'ma10': 'ma10', 'ma20': 'ma20', 'ma60': 'ma60',
    # EMA
    'ema5': 'ema5', 'ema10': 'ema10', 'ema20': 'ema20',
    'ema26': 'ema26', 'ema60': 'ema60', 'ema120': 'ema120',
    # MACD
    'macd_dif': 'macd_dif', 'macd_dea': 'macd_dea', 'macd_hist': 'macd_hist',
    # RSI
    'rsi': 'rsi',
    # 布林带
    'bb_upper': 'bb_upper', 'bb_middle': 'bb_middle',
    'bb_lower': 'bb_lower', 'bb_width': 'bb_width',
    # KDJ
    'kdj_k': 'kdj_k', 'kdj_d': 'kdj_d', 'kdj_j': 'kdj_j',
    # 成交量
    'vol_ma5': 'vol_ma5', 'vol_ma10': 'vol_ma10',
    'volume_ratio': 'volume_ratio', 'mfi14': 'mfi14',
    # 收益率 & 标签
    'return_1d': 'return_1d', 'return_5d': 'return_5d',
    'return_10d': 'return_10d', 'log_return': 'log_return',
    # [防泄漏] future_return / future_direction 不入库
    # 它们只作为训练标签，由 pipeline.py 在划分训练集时临时计算，
    # 绝不能存入 stock_features 表，否则线上预测时可能被误用为特征。
    # 换手率
    'turnover_rate_5': 'turnover_rate_5',
    'turnover_rate_60': 'turnover_rate_60',
    'turnover_rate_120': 'turnover_rate_120',
    # 情绪
    'br': 'br', 'ar': 'ar',
    # 风险（v2.0 更名：原 Variance* → volatility_*）
    'volatility_20d': 'volatility_20d', 'volatility_60d': 'volatility_60d',
    'volatility_120d': 'volatility_120d',
    'skewness_20d': 'skewness_20d', 'skewness_60d': 'skewness_60d',
    'skewness_120d': 'skewness_120d',
    'kurtosis_20d': 'kurtosis_20d', 'kurtosis_60d': 'kurtosis_60d',
    'kurtosis_120d': 'kurtosis_120d',
    # 动量
    'arron_up_25': 'arron_up_25', 'arron_down_25': 'arron_down_25',
    'bear_power': 'bear_power', 'bull_power': 'bull_power',
    'bias5': 'bias5', 'bias10': 'bias10', 'bias20': 'bias20', 'bias60': 'bias60',
    'cci10': 'cci10', 'cci15': 'cci15', 'cci20': 'cci20', 'cci88': 'cci88',
    'cr20': 'cr20', 'mass': 'mass',
    # 技术信号
    'golden_cross': 'golden_cross', 'death_cross': 'death_cross',
    'macd_golden_cross': 'macd_golden_cross',
    'rsi_oversold': 'rsi_oversold', 'rsi_overbought': 'rsi_overbought',
}
