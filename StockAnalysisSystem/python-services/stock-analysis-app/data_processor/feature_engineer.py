# -*- coding: utf-8 -*-
"""
特征工程模块 (Feature Engineering)

职责拆分:
    本模块 ── 单股票时序特征 (time-series)
              按时间顺序为每只股票独立计算的技术指标
    panel_builder ── 全市场横截面特征 (cross-sectional)
                     在同一交易日内跨股票对比计算

空值处理规则 (窗口不足时):
    ┌──────────────────────┬───────────────┬──────────────────────────────────┐
    │ 指标类型             │ NaN 策略      │ 说明                             │
    ├──────────────────────┼───────────────┼──────────────────────────────────┤
    │ 收益率 pct_change    │ 自然 NaN      │ 前 N 行                         │
    │ MA / EMA (talib)     │ 自然 NaN      │ 前 N-1 行                       │
    │ MACD (talib)         │ 自然 NaN      │ 前 slow+signal-2 行             │
    │ RSI (talib)          │ 自然 NaN      │ 前 period 行                    │
    │ 布林带 / KDJ / MASS  │ 自然 NaN      │ pandas rolling 默认行为         │
    │ 风险统计量           │ 自然 NaN      │ [已修正] 不再用 0 填充          │
    │ BR / AR / CR20       │ fillna(0)     │ 分母可能为 0，保留 0 作为中性值 │
    └──────────────────────┴───────────────┴──────────────────────────────────┘

特征命名:
    所有特征统一使用 lowercase snake_case，常量定义在下方「特征名称」段落。

安全保障:
    validate_and_clean() ── 深拷贝原始数据，绝不修改传入对象
    calculate_all_features() ── 内部拷贝，不修改传入对象
"""

import numpy as np
import pandas as pd
import talib

from config.settings import TECHNICAL_INDICATORS
from config.logging_config import get_logger

logger = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  特征版本
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_VERSION = "2.0.0"
"""
特征版本语义：
  MAJOR ── 特征集合有删除或语义变更（不向后兼容）
  MINOR ── 新增特征（向后兼容）
  PATCH ── 算法修复或参数微调

变更记录：
  2.0.0 ── 统一 snake_case 命名；增加 volatility_* 替代旧 Variance*（语义修正）；
           增加横截面特征；去除风险指标 fillna(0)；BB 改用 ddof=0
  1.0.0 ── 初始版本（旧版 MA5 / RSI / Variance20 等命名）
"""


# ═══════════════════════════════════════════════════════════════════════════════
#  特征名称 ── 统一 snake_case，作为唯一命名来源 (single source of truth)
# ═══════════════════════════════════════════════════════════════════════════════

# ── 收益率 ─────────────────────────────────────────────────────────────────────
FEAT_RETURN_1D            = 'return_1d'
FEAT_RETURN_5D            = 'return_5d'
FEAT_RETURN_10D           = 'return_10d'
FEAT_LOG_RETURN           = 'log_return'
FEAT_FUTURE_RETURN_1D     = 'future_return_1d'
FEAT_FUTURE_RETURN_5D     = 'future_return_5d'
FEAT_FUTURE_DIRECTION_1D  = 'future_direction_1d'
FEAT_FUTURE_DIRECTION_5D  = 'future_direction_5d'

# ── 移动平均线 ─────────────────────────────────────────────────────────────────
FEAT_MA5  = 'ma5'
FEAT_MA10 = 'ma10'
FEAT_MA20 = 'ma20'
FEAT_MA60 = 'ma60'

# ── 指数移动平均线 ─────────────────────────────────────────────────────────────
FEAT_EMA5   = 'ema5'
FEAT_EMA10  = 'ema10'
FEAT_EMA20  = 'ema20'
FEAT_EMA26  = 'ema26'
FEAT_EMA60  = 'ema60'
FEAT_EMA120 = 'ema120'

# ── MACD ───────────────────────────────────────────────────────────────────────
FEAT_MACD_DIF    = 'macd_dif'
FEAT_MACD_DEA    = 'macd_dea'
FEAT_MACD_HIST   = 'macd_hist'

# ── RSI ────────────────────────────────────────────────────────────────────────
FEAT_RSI = 'rsi'

# ── 布林带 ─────────────────────────────────────────────────────────────────────
FEAT_BB_UPPER = 'bb_upper'
FEAT_BB_MIDDLE = 'bb_middle'
FEAT_BB_LOWER = 'bb_lower'
FEAT_BB_WIDTH = 'bb_width'

# ── KDJ ────────────────────────────────────────────────────────────────────────
FEAT_KDJ_K = 'kdj_k'
FEAT_KDJ_D = 'kdj_d'
FEAT_KDJ_J = 'kdj_j'

# ── 成交量指标 ─────────────────────────────────────────────────────────────────
FEAT_VOL_MA5     = 'vol_ma5'
FEAT_VOL_MA10    = 'vol_ma10'
FEAT_VOLUME_RATIO = 'volume_ratio'
FEAT_MFI14       = 'mfi14'

# ── 换手率 ─────────────────────────────────────────────────────────────────────
FEAT_TURNOVER_RATE_5   = 'turnover_rate_5'
FEAT_TURNOVER_RATE_60  = 'turnover_rate_60'
FEAT_TURNOVER_RATE_120 = 'turnover_rate_120'

# ── 情绪指标 ───────────────────────────────────────────────────────────────────
FEAT_BR = 'br'
FEAT_AR = 'ar'

# ── 动量指标 ───────────────────────────────────────────────────────────────────
FEAT_ARRON_UP_25   = 'arron_up_25'
FEAT_ARRON_DOWN_25 = 'arron_down_25'
FEAT_BEAR_POWER    = 'bear_power'
FEAT_BULL_POWER    = 'bull_power'
FEAT_BIAS5  = 'bias5'
FEAT_BIAS10 = 'bias10'
FEAT_BIAS20 = 'bias20'
FEAT_BIAS60 = 'bias60'
FEAT_CCI10 = 'cci10'
FEAT_CCI15 = 'cci15'
FEAT_CCI20 = 'cci20'
FEAT_CCI88 = 'cci88'
FEAT_CR20  = 'cr20'
FEAT_MASS  = 'mass'

# ── 风险指标 ───────────────────────────────────────────────────────────────────
# [修正] 旧版名为 Variance*，实际计算的是年化标准差（volatility），此处正名
FEAT_VOLATILITY_20D  = 'volatility_20d'
FEAT_VOLATILITY_60D  = 'volatility_60d'
FEAT_VOLATILITY_120D = 'volatility_120d'
FEAT_SKEWNESS_20D    = 'skewness_20d'
FEAT_SKEWNESS_60D    = 'skewness_60d'
FEAT_SKEWNESS_120D   = 'skewness_120d'
FEAT_KURTOSIS_20D    = 'kurtosis_20d'
FEAT_KURTOSIS_60D    = 'kurtosis_60d'
FEAT_KURTOSIS_120D   = 'kurtosis_120d'

# ── 技术信号 ───────────────────────────────────────────────────────────────────
FEAT_GOLDEN_CROSS    = 'golden_cross'
FEAT_DEATH_CROSS     = 'death_cross'
FEAT_MACD_GOLDEN     = 'macd_golden_cross'
FEAT_RSI_OVERSOLD    = 'rsi_oversold'
FEAT_RSI_OVERBOUGHT  = 'rsi_overbought'

# ── 横截面特征（由 panel_builder.compute_cross_sectional_features 计算）────────
FEAT_MARKET_RETURN       = 'market_return'
FEAT_MARKET_VOL_20D      = 'market_volatility_20d'
FEAT_INDUSTRY_RETURN     = 'industry_return'
FEAT_RETURN_VS_MARKET    = 'return_vs_market'
FEAT_RETURN_VS_INDUSTRY  = 'return_vs_industry'
FEAT_VOL_VS_MARKET       = 'volatility_vs_market'
FEAT_RETURN_ZSCORE       = 'return_zscore_industry'
FEAT_VOL_ZSCORE          = 'volatility_zscore_industry'


# ═══════════════════════════════════════════════════════════════════════════════
#  特征目录 (Feature Catalog) ── 版本化注册表
# ═══════════════════════════════════════════════════════════════════════════════

FEATURE_REGISTRY = {
    'version': FEATURE_VERSION,
    'features': {
        'returns': [
            FEAT_RETURN_1D, FEAT_RETURN_5D, FEAT_RETURN_10D, FEAT_LOG_RETURN,
            FEAT_FUTURE_RETURN_1D, FEAT_FUTURE_RETURN_5D,
            FEAT_FUTURE_DIRECTION_1D, FEAT_FUTURE_DIRECTION_5D,
        ],
        'ma': [FEAT_MA5, FEAT_MA10, FEAT_MA20, FEAT_MA60],
        'ema': [
            FEAT_EMA5, FEAT_EMA10, FEAT_EMA20,
            FEAT_EMA26, FEAT_EMA60, FEAT_EMA120,
        ],
        'macd': [FEAT_MACD_DIF, FEAT_MACD_DEA, FEAT_MACD_HIST],
        'rsi': [FEAT_RSI],
        'bollinger': [FEAT_BB_UPPER, FEAT_BB_MIDDLE, FEAT_BB_LOWER, FEAT_BB_WIDTH],
        'kdj': [FEAT_KDJ_K, FEAT_KDJ_D, FEAT_KDJ_J],
        'volume': [FEAT_VOL_MA5, FEAT_VOL_MA10, FEAT_VOLUME_RATIO, FEAT_MFI14],
        'turnover': [
            FEAT_TURNOVER_RATE_5, FEAT_TURNOVER_RATE_60, FEAT_TURNOVER_RATE_120,
        ],
        'emotion': [FEAT_BR, FEAT_AR],
        'momentum': [
            FEAT_ARRON_UP_25, FEAT_ARRON_DOWN_25,
            FEAT_BEAR_POWER, FEAT_BULL_POWER,
            FEAT_BIAS5, FEAT_BIAS10, FEAT_BIAS20, FEAT_BIAS60,
            FEAT_CCI10, FEAT_CCI15, FEAT_CCI20, FEAT_CCI88,
            FEAT_CR20, FEAT_MASS,
        ],
        'risk': [
            FEAT_VOLATILITY_20D, FEAT_VOLATILITY_60D, FEAT_VOLATILITY_120D,
            FEAT_SKEWNESS_20D, FEAT_SKEWNESS_60D, FEAT_SKEWNESS_120D,
            FEAT_KURTOSIS_20D, FEAT_KURTOSIS_60D, FEAT_KURTOSIS_120D,
        ],
        'signals': [
            FEAT_GOLDEN_CROSS, FEAT_DEATH_CROSS,
            FEAT_MACD_GOLDEN, FEAT_RSI_OVERSOLD, FEAT_RSI_OVERBOUGHT,
        ],
        'cross_sectional': [
            FEAT_MARKET_RETURN, FEAT_MARKET_VOL_20D, FEAT_INDUSTRY_RETURN,
            FEAT_RETURN_VS_MARKET, FEAT_RETURN_VS_INDUSTRY, FEAT_VOL_VS_MARKET,
            FEAT_RETURN_ZSCORE, FEAT_VOL_ZSCORE,
        ],
    }
}


def get_feature_catalog() -> dict:
    """返回完整特征目录（深拷贝），用于外部审计或特征选择。"""
    import copy
    return copy.deepcopy(FEATURE_REGISTRY)


def list_all_features() -> list:
    """返回所有已注册特征名称的扁平列表（不含 label）。"""
    return [f for cat in FEATURE_REGISTRY['features'].values() for f in cat]


# ═══════════════════════════════════════════════════════════════════════════════
#  数据清洗 ── 与特征计算解耦
# ═══════════════════════════════════════════════════════════════════════════════

def validate_and_clean(df: pd.DataFrame) -> pd.DataFrame:
    """
    行情数据预处理（不修改传入对象，始终返回新 DataFrame）。

    步骤:
        1. 深拷贝原始数据
        2. 按 (ts_code, trade_date) 升序排序
        3. 确保 trade_date 为 datetime 类型
        4. 将 ±inf 替换为 NaN（统一空值语义）

    Args:
        df: 原始行情 DataFrame（单股或多股面板均可）

    Returns:
        pd.DataFrame: 清洗后的副本

    Raises:
        ValueError: 缺少核心列
    """
    df = df.copy()

    # 校验核心列
    required = ['open', 'high', 'low', 'close', 'vol']
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"数据缺少核心列: {missing}")

    # 按 (ts_code, trade_date) 排序 ── 保证时序计算顺序正确
    sort_cols = []
    if 'ts_code' in df.columns:
        sort_cols.append('ts_code')
    if 'trade_date' in df.columns:
        sort_cols.append('trade_date')
    if sort_cols:
        df = df.sort_values(sort_cols).reset_index(drop=True)

    # 日期类型统一
    if 'trade_date' in df.columns:
        df['trade_date'] = pd.to_datetime(df['trade_date'])

    # inf → NaN
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    df[numeric_cols] = df[numeric_cols].replace([np.inf, -np.inf], np.nan)

    logger.info(
        "数据清洗完成: %d 行, %s",
        len(df),
        f"{df['ts_code'].nunique()} 只股票" if 'ts_code' in df.columns else "单股票",
    )
    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  FeatureEngineer ── 单股票时序特征
# ═══════════════════════════════════════════════════════════════════════════════

class FeatureEngineer:
    """
    单股票时序特征计算器。

    所有技术指标均按时间顺序为单只股票独立计算。
    横截面特征（市场收益率、行业排名等）由
    ``panel_builder.compute_cross_sectional_features()`` 在全市场面板上计算。

    安全保证：
        calculate_all_features() 内部深拷贝输入，不修改传入的 DataFrame。
    """

    FEATURE_VERSION = FEATURE_VERSION

    def __init__(self):
        cfg = TECHNICAL_INDICATORS
        self.ma_periods   = cfg['ma_periods']
        self.rsi_period   = cfg['rsi_period']
        self.macd_fast    = cfg['macd_fast']
        self.macd_slow    = cfg['macd_slow']
        self.macd_signal  = cfg['macd_signal']
        logger.info("FeatureEngineer v%s 初始化完成", FEATURE_VERSION)

    # ─── 主入口 ────────────────────────────────────────────────────────────────

    def calculate_all_features(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        计算单只股票全部时序特征。

        流程:
            1. 深拷贝输入（保护原始数据）
            2. 按 trade_date 排序
            3. 依次计算：收益率 → 均线 → EMA → 布林带 → KDJ → 量指标 →
                         MACD → RSI → 换手率 → 情绪 → 风险 → 动量 → 信号
            4. 将 ±inf 统一为 NaN

        Args:
            df: 单只股票行情数据（需含 open, high, low, close, vol）

        Returns:
            pd.DataFrame: 在行情列基础上新增特征列（原始列不变）
        """
        if df.empty:
            logger.warning("数据为空，跳过特征计算")
            return df

        # 深拷贝 ── 绝不修改传入对象
        df = df.copy()

        # 按时间排序（单股票内部）
        if 'trade_date' in df.columns:
            df = df.sort_values('trade_date').reset_index(drop=True)

        # 依次计算各类特征
        df = self._calc_returns(df)
        df = self._calc_ma(df)
        df = self._calc_ema(df)
        df = self._calc_bollinger(df)
        df = self._calc_kdj(df)
        df = self._calc_volume(df)
        df = self._calc_macd(df)
        df = self._calc_rsi(df)
        df = self._calc_turnover(df)
        df = self._calc_emotion(df)
        df = self._calc_risk(df)
        df = self._calc_momentum(df)
        df = self._calc_signals(df)

        # 最终 inf 清理
        df = df.replace([np.inf, -np.inf], np.nan)

        new_cols = [
            c for c in df.columns
            if c not in ['trade_date', 'ts_code', 'open', 'high', 'low',
                         'close', 'vol', 'pre_close', 'change', 'pct_chg',
                         'amount', 'turnover_rate']
        ]
        logger.info("时序特征计算完成，共 %d 个特征列", len(new_cols))
        return df

    # ─── 收益率 ────────────────────────────────────────────────────────────────

    def _calc_returns(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        简单收益率 / 对数收益率 / 未来收益率（标签）。
        窗口不足：pct_change(N) 自然产生 NaN（前 N 行）。
        """
        close = df['close']

        # 历史收益率
        df[FEAT_RETURN_1D]  = close.pct_change(1)
        df[FEAT_RETURN_5D]  = close.pct_change(5)
        df[FEAT_RETURN_10D] = close.pct_change(10)
        df[FEAT_LOG_RETURN] = np.log(close / close.shift(1))

        # 未来收益率（预测标签，最后 N 行为 NaN）
        df[FEAT_FUTURE_RETURN_1D] = close.shift(-1) / close - 1
        df[FEAT_FUTURE_RETURN_5D] = close.shift(-5) / close - 1

        # 涨跌方向标签
        df[FEAT_FUTURE_DIRECTION_1D] = (df[FEAT_FUTURE_RETURN_1D] > 0).astype(float).where(df[FEAT_FUTURE_RETURN_1D].notna())
        df[FEAT_FUTURE_DIRECTION_5D] = (df[FEAT_FUTURE_RETURN_5D] > 0).astype(float).where(df[FEAT_FUTURE_RETURN_5D].notna())

        return df

    # ─── 简单移动平均线 ────────────────────────────────────────────────────────

    def _calc_ma(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        MA 均线（talib.SMA，前 N-1 行为 NaN）。
        标准名称: ma5, ma10, ma20, ma60
        """
        _name_map = {5: FEAT_MA5, 10: FEAT_MA10, 20: FEAT_MA20, 60: FEAT_MA60}
        for period in self.ma_periods:
            col = _name_map.get(period, f'ma{period}')
            df[col] = talib.SMA(df['close'], timeperiod=period)
        return df

    # ─── 指数移动平均线 ────────────────────────────────────────────────────────

    def _calc_ema(self, df: pd.DataFrame) -> pd.DataFrame:
        """EMA 指数移动平均线，前 N-1 行为 NaN。"""
        close = df['close']
        df[FEAT_EMA5]   = talib.EMA(close, 5)
        df[FEAT_EMA10]  = talib.EMA(close, 10)
        df[FEAT_EMA20]  = talib.EMA(close, 20)
        df[FEAT_EMA26]  = talib.EMA(close, 26)
        df[FEAT_EMA60]  = talib.EMA(close, 60)
        df[FEAT_EMA120] = talib.EMA(close, 120)
        return df

    # ─── MACD ──────────────────────────────────────────────────────────────────

    def _calc_macd(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        MACD（talib 标准算法，Wilder 平滑）。
        前 (slow + signal - 2) 行为 NaN。
        """
        dif, dea, hist = talib.MACD(
            df['close'],
            fastperiod=self.macd_fast,
            slowperiod=self.macd_slow,
            signalperiod=self.macd_signal,
        )
        df[FEAT_MACD_DIF]  = dif
        df[FEAT_MACD_DEA]  = dea
        df[FEAT_MACD_HIST] = hist
        return df

    # ─── RSI ───────────────────────────────────────────────────────────────────

    def _calc_rsi(self, df: pd.DataFrame, period: int = None) -> pd.DataFrame:
        """
        RSI（talib，Wilder 平滑）。前 period 行为 NaN。
        标准名称: rsi
        """
        if period is None:
            period = self.rsi_period
        df[FEAT_RSI] = talib.RSI(df['close'], timeperiod=period)
        return df

    # ─── 布林带 ────────────────────────────────────────────────────────────────

    def _calc_bollinger(self, df: pd.DataFrame,
                        window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
        """
        布林带（Bollinger Bands）。

        [修正] 使用 ddof=0（总体标准差）与 SMA 语义一致，
               旧代码默认 ddof=1（样本标准差）导致窄幅偏小。

        标准名称: bb_middle, bb_upper, bb_lower, bb_width
        """
        close = df['close']

        mid = close.rolling(window=window).mean()
        std = close.rolling(window=window).std(ddof=0)   # 总体标准差

        upper = mid + num_std * std
        lower = mid - num_std * std
        width = (upper - lower) / mid.replace(0, np.nan)

        df[FEAT_BB_MIDDLE] = mid
        df[FEAT_BB_UPPER]  = upper
        df[FEAT_BB_LOWER]  = lower
        df[FEAT_BB_WIDTH]  = width
        return df

    # ─── KDJ ───────────────────────────────────────────────────────────────────

    def _calc_kdj(self, df: pd.DataFrame,
                  n: int = 9, m1: int = 3, m2: int = 3) -> pd.DataFrame:
        """KDJ 随机指标，前 N 行为 NaN。标准名称: kdj_k, kdj_d, kdj_j"""
        low_min  = df['low'].rolling(window=n).min()
        high_max = df['high'].rolling(window=n).max()
        rsv = (df['close'] - low_min) / (high_max - low_min).replace(0, np.nan) * 100

        k = rsv.ewm(alpha=1 / m1, adjust=False).mean()
        d = k.ewm(alpha=1 / m2, adjust=False).mean()

        df[FEAT_KDJ_K] = k
        df[FEAT_KDJ_D] = d
        df[FEAT_KDJ_J] = 3 * k - 2 * d
        return df

    # ─── 成交量指标 ────────────────────────────────────────────────────────────

    def _calc_volume(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        成交量均线与量比。
        volume_ratio = vol / vol_ma5（当日成交量相对短期均量的倍数）
        """
        vol = df['vol']
        df[FEAT_VOL_MA5]  = vol.rolling(window=5).mean()
        df[FEAT_VOL_MA10] = vol.rolling(window=10).mean()
        df[FEAT_VOLUME_RATIO] = vol / df[FEAT_VOL_MA5].replace(0, np.nan)
        return df

    # ─── MFI ───────────────────────────────────────────────────────────────────

    def _calc_mfi(self, df: pd.DataFrame) -> pd.DataFrame:
        """MFI14 资金流量指标（talib）。"""
        df[FEAT_MFI14] = talib.MFI(
            df['high'], df['low'], df['close'], df['vol'], 14
        )
        return df

    # ─── 换手率 ────────────────────────────────────────────────────────────────

    def _calc_turnover(self, df: pd.DataFrame) -> pd.DataFrame:
        """换手率滚动均值（若 turnover_rate 列存在）。"""
        if 'turnover_rate' not in df.columns:
            return df
        tr = df['turnover_rate']
        df[FEAT_TURNOVER_RATE_5]   = tr.rolling(window=5).mean()
        df[FEAT_TURNOVER_RATE_60]  = tr.rolling(window=60).mean()
        df[FEAT_TURNOVER_RATE_120] = tr.rolling(window=120).mean()
        return df

    # ─── 情绪指标 ──────────────────────────────────────────────────────────────

    def _calc_emotion(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        BR（买卖意愿）/ AR（情绪指标）。
        分母为 0 时填充 0（中性值），其余窗口不足时为 NaN。
        """
        close_s1 = df['close'].shift(1)

        # BR = sum(H - L_{t-1}, 26) / sum(C_{t-1} - L, 26) * 100
        br_num = (df['high'] - df['low'].shift(1)).rolling(window=26).sum()
        br_den = (close_s1 - df['low']).rolling(window=26).sum()
        df[FEAT_BR] = (br_num / br_den.replace(0, np.nan) * 100).fillna(0)

        # AR = sum(H - O, 26) / sum(O - L, 26) * 100
        ar_num = (df['high'] - df['open']).rolling(window=26).sum()
        ar_den = (df['open'] - df['low']).rolling(window=26).sum()
        df[FEAT_AR] = (ar_num / ar_den.replace(0, np.nan) * 100).fillna(0)

        return df

    # ─── 风险指标 ──────────────────────────────────────────────────────────────

    def _calc_risk(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        年化波动率 / 偏度 / 峰度。

        [修正] 旧版将年化标准差误命名为 Variance*，语义实为波动率，
               此处更名为 volatility_*d。
        [修正] 窗口不足时保留 NaN（旧版 fillna(0) 会在样本稀少时引入虚假信号）。
        """
        ret = df[FEAT_RETURN_1D]

        # 年化波动率 = std(daily_return) * √252
        df[FEAT_VOLATILITY_20D]  = ret.rolling(window=20).std()  * np.sqrt(252)
        df[FEAT_VOLATILITY_60D]  = ret.rolling(window=60).std()  * np.sqrt(252)
        df[FEAT_VOLATILITY_120D] = ret.rolling(window=120).std() * np.sqrt(252)

        # 偏度（rolling 窗口不足自然为 NaN）
        df[FEAT_SKEWNESS_20D]  = ret.rolling(window=20).skew()
        df[FEAT_SKEWNESS_60D]  = ret.rolling(window=60).skew()
        df[FEAT_SKEWNESS_120D] = ret.rolling(window=120).skew()

        # 峰度
        df[FEAT_KURTOSIS_20D]  = ret.rolling(window=20).kurt()
        df[FEAT_KURTOSIS_60D]  = ret.rolling(window=60).kurt()
        df[FEAT_KURTOSIS_120D] = ret.rolling(window=120).kurt()

        return df

    # ─── 动量指标 ──────────────────────────────────────────────────────────────

    def _calc_momentum(self, df: pd.DataFrame) -> pd.DataFrame:
        """Aroon / BIAS / CCI / CR / MASS / 多空力道。"""
        close = df['close']
        high  = df['high']
        low   = df['low']

        # Aroon（talib 返回 (down, up)，取索引）
        aroon_down, aroon_up = talib.AROON(high, low, 25)
        df[FEAT_ARRON_UP_25]   = aroon_up
        df[FEAT_ARRON_DOWN_25] = aroon_down

        # 多空力道
        ema13 = talib.EMA(close, 13)
        df[FEAT_BEAR_POWER] = (low  - ema13) / close.replace(0, np.nan)
        df[FEAT_BULL_POWER] = (high - ema13) / close.replace(0, np.nan)

        # BIAS 乖离率 = (close - MA_N) / MA_N * 100
        for n, feat in [(5, FEAT_BIAS5), (10, FEAT_BIAS10),
                        (20, FEAT_BIAS20), (60, FEAT_BIAS60)]:
            ma_n = talib.MA(close, n)
            df[feat] = (close - ma_n) / ma_n.replace(0, np.nan) * 100

        # CCI 顺势指标
        df[FEAT_CCI10] = talib.CCI(high, low, close, 10)
        df[FEAT_CCI15] = talib.CCI(high, low, close, 15)
        df[FEAT_CCI20] = talib.CCI(high, low, close, 20)
        df[FEAT_CCI88] = talib.CCI(high, low, close, 88)

        # CR20 = sum(|H - mid_{t-1}|, 20) / sum(|mid_{t-1} - L|, 20) * 100
        mid_prev = (high.shift(1) + low.shift(1)) / 2
        up_str   = np.maximum(high - mid_prev, 0).rolling(window=20).sum()
        dn_str   = np.maximum(mid_prev - low, 0).rolling(window=20).sum()
        df[FEAT_CR20] = (up_str / dn_str.replace(0, np.nan) * 100).fillna(0)

        # MASS 梅斯线
        hl_range = high - low
        ema1 = hl_range.ewm(span=9,  adjust=False).mean()
        ema2 = ema1.ewm(span=25, adjust=False).mean()
        ratio = ema1 / ema2.replace(0, np.nan)
        df[FEAT_MASS] = ratio.rolling(window=25).sum().fillna(0)

        return df

    # ─── 技术信号（交叉 / 超买超卖）───────────────────────────────────────────

    def _calc_signals(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        基于已计算特征派生的二值信号。
        依赖：ma5, ma10, macd_dif, macd_dea, rsi（须先于本方法计算）。
        """
        ma5  = df[FEAT_MA5]
        ma10 = df[FEAT_MA10]

        # MA 金叉 / 死叉
        df[FEAT_GOLDEN_CROSS] = (
            (ma5 > ma10) & (ma5.shift(1) <= ma10.shift(1))
        ).astype(int)
        df[FEAT_DEATH_CROSS] = (
            (ma5 < ma10) & (ma5.shift(1) >= ma10.shift(1))
        ).astype(int)

        # MACD 金叉
        dif  = df[FEAT_MACD_DIF]
        dea  = df[FEAT_MACD_DEA]
        df[FEAT_MACD_GOLDEN] = (
            (dif > dea) & (dif.shift(1) <= dea.shift(1))
        ).astype(int)

        # RSI 超买 / 超卖
        rsi = df[FEAT_RSI]
        df[FEAT_RSI_OVERSOLD]   = (rsi < 30).astype(int)
        df[FEAT_RSI_OVERBOUGHT] = (rsi > 70).astype(int)

        return df

    # ─── 并行计算 ──────────────────────────────────────────────────────────────

    def calculate_features_parallel(
        self,
        panel_df: pd.DataFrame,
        n_workers: int = None,
        code_column: str = 'ts_code',
        date_column: str = 'trade_date',
        forward_days: int = 5,
    ) -> pd.DataFrame:
        """
        多线程并行计算全市场面板的时序特征。

        按股票代码拆分，每只股票独立计算（保证时序不跨股），
        TA-Lib 底层为 C 实现，线程并行可有效加速。

        Args:
            panel_df:     全市场面板数据
            n_workers:    线程数，默认 8
            code_column:  股票代码列名
            date_column:  日期列名
            forward_days: 远期收益率天数（保留参数，暂未使用）

        Returns:
            pd.DataFrame: 拼接后的全市场特征面板
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        if n_workers is None:
            n_workers = 8

        logger.info("启动并行特征计算（%d 线程）", n_workers)

        codes = panel_df[code_column].unique()
        tasks = []
        for code in codes:
            stock_df = panel_df[panel_df[code_column] == code].copy()
            stock_df = stock_df.sort_values(date_column).reset_index(drop=True)
            if len(stock_df) >= 60:
                tasks.append((code, stock_df, forward_days))

        total = len(tasks)
        logger.info("共 %d 只股票待计算（数据行数 ≥ 60）", total)

        results     = []
        success_cnt = 0
        fail_cnt    = 0

        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            futures = {
                executor.submit(_compute_single_stock, t): t[0]
                for t in tasks
            }
            for i, future in enumerate(as_completed(futures), 1):
                code, result, error = future.result()
                if error is None:
                    results.append(result)
                    success_cnt += 1
                else:
                    fail_cnt += 1
                    if fail_cnt <= 3:
                        logger.warning("股票 %s 计算失败: %s", code, error)

                if i % 100 == 0 or i == total:
                    logger.info(
                        "进度: %d/%d  成功=%d  失败=%d",
                        i, total, success_cnt, fail_cnt,
                    )

        if not results:
            logger.warning("无股票成功计算特征，返回空 DataFrame")
            return pd.DataFrame()

        featured = pd.concat(results, ignore_index=True)
        logger.info(
            "并行计算完成: %d 行, %d 只股票, 成功=%d, 失败=%d",
            len(featured),
            featured[code_column].nunique(),
            success_cnt,
            fail_cnt,
        )
        return featured


def _compute_single_stock(args):
    """
    线程任务：计算单只股票全部特征。

    Args:
        args: (code, stock_df, forward_days)

    Returns:
        (code, result_df, None) | (code, None, error_str)
    """
    code, stock_df, _forward_days = args
    engineer = FeatureEngineer()
    try:
        result = engineer.calculate_all_features(stock_df)
        return code, result, None
    except Exception as exc:
        return code, None, str(exc)
