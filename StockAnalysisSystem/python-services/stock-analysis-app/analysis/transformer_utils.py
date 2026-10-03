# -*- coding: utf-8 -*-
"""
Transformer 工具函数

基于 app/code/src/utils.py 迁移，包含：
- 39个技术指标特征（TA-Lib）
- 158个Alpha158风格特征
- 8个截面相对特征
- 向量化数据集构建
"""
from config.logging_config import get_logger
logger = get_logger(__name__)

import os
import pandas as pd
import numpy as np
from tqdm import tqdm


# ============================================================
# 特征工程
# ============================================================

def _rolling_linear_regression(x, y):
    x = np.vstack([np.ones(len(x)), x]).T
    beta, res, _, _ = np.linalg.lstsq(x, y, rcond=None)
    return beta[1], res[0] if len(res) > 0 else 0.0, np.sum((y - (x @ beta))**2)


def engineer_features_39(df):
    """
    计算39个技术指标特征。
    使用 TA-Lib 加速计算。
    """
    try:
        import talib
    except ImportError:
        raise ImportError("请安装TA-Lib库: pip install TA-Lib")

    df = df.copy()

    open_ = df['开盘'].astype(float)
    high = df['最高'].astype(float)
    low = df['最低'].astype(float)
    close = df['收盘'].astype(float)
    volume = df['成交量'].astype(float)

    # 移动平均线
    df['sma_5'] = talib.SMA(close, timeperiod=5)
    df['sma_20'] = talib.SMA(close, timeperiod=20)
    df['ema_12'] = talib.EMA(close, timeperiod=12)
    df['ema_26'] = talib.EMA(close, timeperiod=26)
    df['ema_60'] = talib.EMA(close, timeperiod=60)

    # MACD
    macd_line, macd_signal_line, macd_hist = talib.MACD(close, fastperiod=12, slowperiod=26, signalperiod=9)
    df['macd'] = macd_line
    df['macd_signal'] = macd_signal_line

    # RSI
    df['rsi'] = talib.RSI(close, timeperiod=14)

    # KDJ
    df['kdj_k'], df['kdj_d'] = talib.STOCH(high, low, close, fastk_period=9, slowk_period=3, slowd_period=3)
    df['kdj_j'] = 3 * df['kdj_k'] - 2 * df['kdj_d']

    # Bollinger Bands
    df['boll_mid'], df['boll_upper'], df['boll_lower'] = talib.BBANDS(close, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
    df['boll_std'] = (df['boll_upper'] - df['boll_mid']) / 2
    df.drop(columns=['boll_upper', 'boll_lower'], inplace=True)

    # ATR
    df['atr_14'] = talib.ATR(high, low, close, timeperiod=14)

    # OBV
    df['obv'] = talib.OBV(close, volume)

    # Volume features
    df['volume_change'] = volume.pct_change()
    df['volume_ma_5'] = talib.SMA(volume, timeperiod=5)
    df['volume_ma_20'] = talib.SMA(volume, timeperiod=20)
    df['volume_ratio'] = df['volume_ma_5'] / df['volume_ma_20']

    # Returns and Volatility
    df['return_1'] = close.pct_change(1)
    df['return_5'] = close.pct_change(5)
    df['return_10'] = close.pct_change(10)
    df['volatility_10'] = df['return_1'].rolling(10).std()
    df['volatility_20'] = df['return_1'].rolling(20).std()

    # Spreads
    df['high_low_spread'] = high - low
    df['open_close_spread'] = open_ - close
    df['high_close_spread'] = high - close
    df['low_close_spread'] = low - close

    # Daily Standard Deviation
    returns = close.pct_change()
    df['daily_standard_deviation'] = returns.ewm(halflife=42, min_periods=252).std()

    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.fillna(0, inplace=True)

    return df


def engineer_features(df):
    """
    计算158个Alpha158风格特征，使用 talib 加速。
    """
    try:
        import talib
    except ImportError:
        raise ImportError("请安装TA-Lib库: pip install TA-Lib")

    df = df.copy()

    open_ = df['开盘'].astype(float)
    high = df['最高'].astype(float)
    low = df['最低'].astype(float)
    close = df['收盘'].astype(float)
    volume = df['成交量'].astype(float)
    vwap = df['成交额'] / (volume + 1e-12)

    features = []
    feature_names = []

    # 1. K-line features (9)
    features.extend([
        (close - open_) / (open_ + 1e-12),
        (high - low) / (open_ + 1e-12),
        (close - open_) / (high - low + 1e-12),
        (high - pd.concat([open_, close], axis=1).max(axis=1)) / (open_ + 1e-12),
        (high - pd.concat([open_, close], axis=1).max(axis=1)) / (high - low + 1e-12),
        (pd.concat([open_, close], axis=1).min(axis=1) - low) / (open_ + 1e-12),
        (pd.concat([open_, close], axis=1).min(axis=1) - low) / (high - low + 1e-12),
        (2 * close - high - low) / (open_ + 1e-12),
        (2 * close - high - low) / (high - low + 1e-12)
    ])
    feature_names.extend(['KMID', 'KLEN', 'KMID2', 'KUP', 'KUP2', 'KLOW', 'KLOW2', 'KSFT', 'KSFT2'])

    # 2. Price-related features (4)
    features.extend([
        open_ / (close + 1e-12),
        high / (close + 1e-12),
        low / (close + 1e-12),
        vwap / (close + 1e-12)
    ])
    feature_names.extend(['OPEN0', 'HIGH0', 'LOW0', 'VWAP0'])

    windows = [5, 10, 20, 30, 60]

    # 3. Price change features (5)
    for w in windows:
        features.append(close.shift(w) / (close + 1e-12))
        feature_names.append(f'ROC{w}')

    # 4. Moving average features (5)
    for w in windows:
        features.append(talib.SMA(close, timeperiod=w) / (close + 1e-12))
        feature_names.append(f'MA{w}')

    # 5. Standard deviation features (5)
    for w in windows:
        features.append(talib.STDDEV(close, timeperiod=w) / (close + 1e-12))
        feature_names.append(f'STD{w}')

    # 6. Regression-based features (15)
    for w in windows:
        slope = talib.LINEARREG_SLOPE(close, timeperiod=w)
        features.append(slope / (close + 1e-12))
        feature_names.append(f'BETA{w}')

        x = np.arange(len(close))
        time_series = pd.Series(x, index=close.index)
        rolling_corr = close.rolling(w).corr(time_series)
        rsquare = rolling_corr**2
        features.append(rsquare)
        feature_names.append(f'RSQR{w}')

        intercept = talib.LINEARREG_INTERCEPT(close, timeperiod=w)
        predicted = slope * (w - 1) + intercept
        resi = close - predicted
        features.append(resi / (close + 1e-12))
        feature_names.append(f'RESI{w}')

    # 7. Max/Min features (10)
    for w in windows:
        features.append(talib.MAX(high, timeperiod=w) / (close + 1e-12))
        feature_names.append(f'MAX{w}')
    for w in windows:
        features.append(talib.MIN(low, timeperiod=w) / (close + 1e-12))
        feature_names.append(f'MIN{w}')

    # 8. Quantile features (10)
    for w in windows:
        features.append(close.rolling(w).quantile(0.8) / (close + 1e-12))
        feature_names.append(f'QTLU{w}')
    for w in windows:
        features.append(close.rolling(w).quantile(0.2) / (close + 1e-12))
        feature_names.append(f'QTLD{w}')

    # 9. Rank features (5)
    for w in windows:
        features.append(close.rolling(w).rank(pct=True))
        feature_names.append(f'RANK{w}')

    # 10. Stochastic oscillator features (5)
    for w in windows:
        min_low = low.rolling(w).min()
        max_high = high.rolling(w).max()
        features.append((close - min_low) / (max_high - min_low + 1e-12))
        feature_names.append(f'RSV{w}')

    # 11. Index of Max/Min features (15)
    for w in windows:
        features.append(high.rolling(w).apply(np.argmax, raw=True) / w)
        feature_names.append(f'IMAX{w}')
    for w in windows:
        features.append(low.rolling(w).apply(np.argmin, raw=True) / w)
        feature_names.append(f'IMIN{w}')
    for w in windows:
        imax = high.rolling(w).apply(np.argmax, raw=True)
        imin = low.rolling(w).apply(np.argmin, raw=True)
        features.append((imax - imin) / w)
        feature_names.append(f'IMXD{w}')

    # 12. Correlation features (10)
    log_volume = np.log(volume + 1)
    for w in windows:
        features.append(talib.CORREL(close, log_volume, timeperiod=w))
        feature_names.append(f'CORR{w}')

    close_ret = close / close.shift(1)
    volume_ret = volume / (volume.shift(1) + 1e-12)
    log_volume_ret = np.log(volume_ret + 1)
    for w in windows:
        corr_df = pd.concat([close_ret, log_volume_ret], axis=1).fillna(0)
        features.append(talib.CORREL(corr_df.iloc[:, 0], corr_df.iloc[:, 1], timeperiod=w))
        feature_names.append(f'CORD{w}')

    # 13. Count features (15)
    close_diff_pos = (close > close.shift(1))
    close_diff_neg = (close < close.shift(1))
    for w in windows:
        features.append(close_diff_pos.rolling(w).mean())
        feature_names.append(f'CNTP{w}')
    for w in windows:
        features.append(close_diff_neg.rolling(w).mean())
        feature_names.append(f'CNTN{w}')
    for w in windows:
        cntp = close_diff_pos.rolling(w).mean()
        cntn = close_diff_neg.rolling(w).mean()
        features.append(cntp - cntn)
        feature_names.append(f'CNTD{w}')

    # 14. Sum of price change features (15)
    close_diff_abs = (close - close.shift(1)).abs()
    close_diff_up = (close - close.shift(1)).clip(lower=0)
    close_diff_down = -(close - close.shift(1)).clip(upper=0)
    for w in windows:
        sum_abs = close_diff_abs.rolling(w).sum()
        sum_up = close_diff_up.rolling(w).sum()
        features.append(sum_up / (sum_abs + 1e-12))
        feature_names.append(f'SUMP{w}')
    for w in windows:
        sum_abs = close_diff_abs.rolling(w).sum()
        sum_down = close_diff_down.rolling(w).sum()
        features.append(sum_down / (sum_abs + 1e-12))
        feature_names.append(f'SUMN{w}')
    for w in windows:
        sum_abs = close_diff_abs.rolling(w).sum()
        sum_up = close_diff_up.rolling(w).sum()
        sum_down = close_diff_down.rolling(w).sum()
        features.append((sum_up - sum_down) / (sum_abs + 1e-12))
        feature_names.append(f'SUMD{w}')

    # 15. Volume-related features (10)
    for w in windows:
        features.append(talib.SMA(volume, timeperiod=w) / (volume + 1e-12))
        feature_names.append(f'VMA{w}')
    for w in windows:
        features.append(talib.STDDEV(volume, timeperiod=w) / (volume + 1e-12))
        feature_names.append(f'VSTD{w}')

    # 16. Weighted volume features (5)
    vol_weighted_ret = (close / close.shift(1) - 1).abs() * volume
    for w in windows:
        mean_vol_w_ret = vol_weighted_ret.rolling(w).mean()
        std_vol_w_ret = vol_weighted_ret.rolling(w).std()
        features.append(std_vol_w_ret / (mean_vol_w_ret + 1e-12))
        feature_names.append(f'WVMA{w}')

    # 17. Volume change sum features (15)
    volume_diff_abs = (volume - volume.shift(1)).abs()
    volume_diff_up = (volume - volume.shift(1)).clip(lower=0)
    volume_diff_down = -(volume - volume.shift(1)).clip(upper=0)
    for w in windows:
        sum_abs = volume_diff_abs.rolling(w).sum()
        sum_up = volume_diff_up.rolling(w).sum()
        features.append(sum_up / (sum_abs + 1e-12))
        feature_names.append(f'VSUMP{w}')
    for w in windows:
        sum_abs = volume_diff_abs.rolling(w).sum()
        sum_down = volume_diff_down.rolling(w).sum()
        features.append(sum_down / (sum_abs + 1e-12))
        feature_names.append(f'VSUMN{w}')
    for w in windows:
        sum_abs = volume_diff_abs.rolling(w).sum()
        sum_up = volume_diff_up.rolling(w).sum()
        sum_down = volume_diff_down.rolling(w).sum()
        features.append((sum_up - sum_down) / (sum_abs + 1e-12))
        feature_names.append(f'VSUMD{w}')

    feature_df = pd.concat(features, axis=1)
    feature_df.columns = feature_names

    df = pd.concat([df, feature_df], axis=1)
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
    df.fillna(0, inplace=True)
    return df


def engineer_features_158plus39(df):
    """
    计算158个Alpha特征和39个技术指标特征，并合并。
    """
    df_copy = df.copy()

    # 1. 计算158个Alpha特征
    df_158 = engineer_features(df_copy)

    # 2. 计算39个技术指标特征
    df_39 = engineer_features_39(df_copy)

    # 3. 合并
    feature_cols_39 = [
        'sma_5', 'sma_20', 'ema_12', 'ema_26', 'rsi', 'macd', 'macd_signal',
        'volume_change', 'obv', 'volume_ma_5', 'volume_ma_20', 'volume_ratio',
        'kdj_k', 'kdj_d', 'kdj_j', 'boll_mid', 'boll_std', 'atr_14', 'ema_60',
        'volatility_10', 'volatility_20', 'return_1', 'return_5', 'return_10',
        'high_low_spread', 'open_close_spread', 'high_close_spread', 'low_close_spread',
        'daily_standard_deviation',
        'cs_return_pct', 'cs_volume_pct', 'cs_turnover_pct', 'cs_amplitude_pct',
        'cs_excess_return', 'cs_price_deviation', 'cs_price_pct', 'cs_relative_amplitude'
    ]

    feature_cols_39_exist = [col for col in feature_cols_39 if col in df_39.columns]
    df_final = pd.concat([df_158, df_39[feature_cols_39_exist]], axis=1)
    df_final = df_final.loc[:, ~df_final.columns.duplicated()]
    df_final.replace([np.inf, -np.inf], np.nan, inplace=True)
    df_final.fillna(0, inplace=True)

    return df_final


def add_cross_sectional_features(df):
    """
    在每个交易日截面上计算相对特征。
    输入: 已完成 per-stock 特征工程的 DataFrame，需包含 '日期' 列
    输出: 添加了 8 个截面特征的同一 DataFrame
    使用纯 numpy 操作，避免 pandas 索引对齐导致的内存爆炸。
    """
    from scipy.stats import rankdata

    if '日期' not in df.columns:
        return df

    df['日期'] = pd.to_datetime(df['日期'])
    n = len(df)

    # 预分配数组
    cs_return_pct = np.zeros(n, dtype=np.float32)
    cs_volume_pct = np.zeros(n, dtype=np.float32)
    cs_turnover_pct = np.zeros(n, dtype=np.float32)
    cs_amplitude_pct = np.zeros(n, dtype=np.float32)
    cs_excess_return = np.zeros(n, dtype=np.float32)
    cs_price_deviation = np.zeros(n, dtype=np.float32)
    cs_price_pct = np.zeros(n, dtype=np.float32)
    cs_relative_amplitude = np.zeros(n, dtype=np.float32)

    has_turnover = '换手率' in df.columns

    for date, group in df.groupby('日期'):
        idx = group.index.values
        gsize = len(group)
        if gsize < 10:
            continue

        ret = group['涨跌幅'].values.astype(np.float64)
        vol = group['成交量'].values.astype(np.float64)
        amp = group['振幅'].values.astype(np.float64)
        close = group['收盘'].values.astype(np.float64)

        cs_return_pct[idx] = rankdata(ret, method='average') / gsize
        cs_volume_pct[idx] = rankdata(vol, method='average') / gsize

        if has_turnover:
            tvol = group['换手率'].values.astype(np.float64)
            cs_turnover_pct[idx] = rankdata(tvol, method='average') / gsize

        cs_amplitude_pct[idx] = rankdata(amp, method='average') / gsize

        cs_mean_ret = ret.mean()
        cs_excess_return[idx] = ret - cs_mean_ret

        cs_median_close = np.median(close)
        cs_price_deviation[idx] = (close - cs_median_close) / (cs_median_close + 1e-12)

        cs_price_pct[idx] = rankdata(close, method='average') / gsize

        cs_median_amp = np.median(amp)
        cs_relative_amplitude[idx] = amp / (cs_median_amp + 1e-12)

    df['cs_return_pct'] = cs_return_pct
    df['cs_volume_pct'] = cs_volume_pct
    df['cs_turnover_pct'] = cs_turnover_pct
    df['cs_amplitude_pct'] = cs_amplitude_pct
    df['cs_excess_return'] = cs_excess_return
    df['cs_price_deviation'] = cs_price_deviation
    df['cs_price_pct'] = cs_price_pct
    df['cs_relative_amplitude'] = cs_relative_amplitude

    # 逐列清理 inf/NaN，避免 df[cs_cols] 一次性操作触发 DataFrame consolidate
    for col in ['cs_return_pct', 'cs_volume_pct', 'cs_turnover_pct', 'cs_amplitude_pct',
                'cs_excess_return', 'cs_price_deviation', 'cs_price_pct', 'cs_relative_amplitude']:
        if col in df.columns:
            vals = df[col].values
            vals = np.where(np.isfinite(vals), vals, 0.0).astype(np.float32)
            df[col] = vals

    return df


# ============================================================
# 特征列定义
# ============================================================

FEATURE_COLUMNS_MAP = {
    '39': [
        'instrument', '开盘', '收盘', '最高', '最低', '成交量', '成交额', '振幅', '涨跌额', '涨跌幅',
        'sma_5', 'sma_20', 'ema_12', 'ema_26', 'rsi', 'macd', 'macd_signal', 'volume_change', 'obv',
        'volume_ma_5', 'volume_ma_20', 'volume_ratio', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_mid', 'boll_std',
        'atr_14', 'ema_60', 'volatility_10', 'volatility_20', 'return_1', 'return_5', 'return_10',
        'high_low_spread', 'open_close_spread', 'high_close_spread', 'low_close_spread', 'daily_standard_deviation'
    ],
    '158+39': [
        'instrument', '开盘', '收盘', '最高', '最低', '成交量', '成交额', '振幅', '涨跌额', '涨跌幅',
        'KMID', 'KLEN', 'KMID2', 'KUP', 'KUP2', 'KLOW', 'KLOW2', 'KSFT', 'KSFT2', 'OPEN0', 'HIGH0', 'LOW0',
        'VWAP0', 'ROC5', 'ROC10', 'ROC20', 'ROC30', 'ROC60', 'MA5', 'MA10', 'MA20', 'MA30', 'MA60', 'STD5',
        'STD10', 'STD20', 'STD30', 'STD60', 'BETA5', 'BETA10', 'BETA20', 'BETA30', 'BETA60', 'RSQR5', 'RSQR10',
        'RSQR20', 'RSQR30', 'RSQR60', 'RESI5', 'RESI10', 'RESI20', 'RESI30', 'RESI60', 'MAX5', 'MAX10', 'MAX20',
        'MAX30', 'MAX60', 'MIN5', 'MIN10', 'MIN20', 'MIN30', 'MIN60', 'QTLU5', 'QTLU10', 'QTLU20', 'QTLU30',
        'QTLU60', 'QTLD5', 'QTLD10', 'QTLD20', 'QTLD30', 'QTLD60', 'RANK5', 'RANK10', 'RANK20', 'RANK30',
        'RANK60', 'RSV5', 'RSV10', 'RSV20', 'RSV30', 'RSV60', 'IMAX5', 'IMAX10', 'IMAX20', 'IMAX30', 'IMAX60',
        'IMIN5', 'IMIN10', 'IMIN20', 'IMIN30', 'IMIN60', 'IMXD5', 'IMXD10', 'IMXD20', 'IMXD30', 'IMXD60',
        'CORR5', 'CORR10', 'CORR20', 'CORR30', 'CORR60', 'CORD5', 'CORD10', 'CORD20', 'CORD30', 'CORD60',
        'CNTP5', 'CNTP10', 'CNTP20', 'CNTP30', 'CNTP60', 'CNTN5', 'CNTN10', 'CNTN20', 'CNTN30', 'CNTN60',
        'CNTD5', 'CNTD10', 'CNTD20', 'CNTD30', 'CNTD60', 'SUMP5', 'SUMP10', 'SUMP20', 'SUMP30', 'SUMP60',
        'SUMN5', 'SUMN10', 'SUMN20', 'SUMN30', 'SUMN60', 'SUMD5', 'SUMD10', 'SUMD20', 'SUMD30', 'SUMD60',
        'VMA5', 'VMA10', 'VMA20', 'VMA30', 'VMA60', 'VSTD5', 'VSTD10', 'VSTD20', 'VSTD30', 'VSTD60', 'WVMA5',
        'WVMA10', 'WVMA20', 'WVMA30', 'WVMA60', 'VSUMP5', 'VSUMP10', 'VSUMP20', 'VSUMP30', 'VSUMP60', 'VSUMN5',
        'VSUMN10', 'VSUMN20', 'VSUMN30', 'VSUMN60', 'VSUMD5', 'VSUMD10', 'VSUMD20', 'VSUMD30', 'VSUMD60',
        'sma_5', 'sma_20', 'ema_12', 'ema_26', 'rsi', 'macd', 'macd_signal', 'volume_change', 'obv',
        'volume_ma_5', 'volume_ma_20', 'volume_ratio', 'kdj_k', 'kdj_d', 'kdj_j', 'boll_mid', 'boll_std',
        'atr_14', 'ema_60', 'volatility_10', 'volatility_20', 'return_1', 'return_5', 'return_10',
        'high_low_spread', 'open_close_spread', 'high_close_spread', 'low_close_spread', 'daily_standard_deviation',
        'cs_return_pct', 'cs_volume_pct', 'cs_amplitude_pct',
        'cs_excess_return', 'cs_price_deviation', 'cs_price_pct', 'cs_relative_amplitude'
    ]
}

FEATURE_ENGINEER_FUNC_MAP = {
    '39': engineer_features_39,
    '158+39': engineer_features_158plus39,
}


# ============================================================
# 向量化数据集构建
# ============================================================

def create_ranking_dataset_vectorized(data, features, sequence_length,
                                       ranking_data_path=None, min_window_end_date=None):
    """
    按日期直接聚合滑动窗口，避免创建巨型中间 DataFrame。
    保持与原函数完全相同的输出格式。

    策略：先按日期分组，对每个日期只保留最近 sequence_length 天的数据，
    避免将所有窗口缓存在内存中。
    """
    if type(sequence_length) is not int or sequence_length <= 0:
        raise ValueError('sequence_length must be a positive integer')
    data = data.copy()
    data.rename(columns={'trade_date': 'datetime', '日期': 'datetime'}, inplace=True)
    if 'datetime' not in data.columns:
        raise ValueError("数据中缺少 'datetime' 或 'trade_date' / '日期' 列")

    data['datetime'] = pd.to_datetime(data['datetime'])
    data = data.sort_values(['instrument', 'datetime']).reset_index(drop=True)
    if data.duplicated(['instrument', 'datetime']).any():
        raise ValueError('Duplicate instrument/date session')

    if min_window_end_date is not None:
        min_window_end_date = pd.to_datetime(min_window_end_date)

    # 获取所有唯一日期
    all_dates = sorted(data['datetime'].unique())
    n_dates = len(all_dates)

    # 按股票分组的数据
    stock_data = {}
    for stock_code, group in data.groupby('instrument'):
        group = group.sort_values('datetime').reset_index(drop=True)
        stock_data[stock_code] = {
            'dates': group['datetime'].values,
            'features': group[features].values.astype(np.float32),
            'labels': group['label'].values.astype(np.float32),
        }

    sequences = []
    targets = []
    relevance_scores = []
    stock_indices = []
    cls_labels_list = []
    dir_labels_list = []

    logger.info("Step 1: 按日期聚合滑动窗口...")
    for di in tqdm(range(n_dates), desc="Processing dates"):
        date = all_dates[di]

        if min_window_end_date is not None and date < min_window_end_date:
            continue

        # Labels already encode their future horizon; context is historical only.
        if di < sequence_length - 1:
            continue

        # 收集当天有数据的所有股票
        day_seqs = []
        day_targets = []
        day_codes = []

        for stock_code, sdata in stock_data.items():
            # 找到该股票在 date 这一天对应的索引
            date_mask = sdata['dates'] == date
            if not np.any(date_mask):
                continue
            end_idx = np.argmax(date_mask)

            if end_idx < sequence_length - 1 or not np.isfinite(sdata['labels'][end_idx]):
                continue

            start_idx = end_idx - sequence_length + 1
            seq = sdata['features'][start_idx:end_idx+1]
            if not np.isfinite(seq).all():
                continue
            day_seqs.append(seq)
            day_targets.append(sdata['labels'][end_idx])
            day_codes.append(stock_code)

        n_stocks = len(day_codes)
        if n_stocks < 10:
            continue

        day_seqs = np.stack(day_seqs)
        day_targets = np.array(day_targets, dtype=np.float32)

        sorted_indices = np.argsort(day_targets)[::-1]
        relevance = np.zeros_like(day_targets, dtype=np.float32)
        for rank, idx in enumerate(sorted_indices):
            relevance[idx] = n_stocks - rank

        day_median = np.median(day_targets)
        cls_labels = (day_targets > day_median).astype(np.float32)
        dir_labels = (day_targets > 0).astype(np.float32)

        sequences.append(day_seqs)
        targets.append(day_targets)
        relevance_scores.append(relevance)
        stock_indices.append(day_codes)
        cls_labels_list.append(cls_labels)
        dir_labels_list.append(dir_labels)

    logger.info(f"成功创建 {len(sequences)} 个训练样本")
    if len(sequences) > 0:
        avg_stocks = np.mean([len(seq) for seq in sequences])
        logger.info(f"每个样本平均包含 {avg_stocks:.1f} 只股票")

    return sequences, targets, relevance_scores, stock_indices, cls_labels_list, dir_labels_list
