"""
数据清洗模块

职责（V0.1 单机版）：
    1. 统一字段名称（中文/别名 → 英文标准列名）
    2. 校验股票代码格式（ts_code: 6位数字.交易所后缀）
    3. 统一交易日期为 datetime 类型
    4. 强制数值列类型转换
    5. 移除核心字段含空值的行
    6. 基于业务键 [ts_code, trade_date] 去重
    7. 检测并移除价格逻辑异常（close<=0, high<low, open越界）
    8. 检测并移除成交量异常（vol<0）
    9. 按 [ts_code, trade_date] 升序排列

唯一业务键：ts_code + trade_date

清洗前后统计：输入行数、空值行数、重复行数、价格异常行数、
              成交量异常行数、输出行数 —— 通过 CleanReport 暴露。

不负责：
    - 技术指标计算（由 data_processor.feature_engineer 处理）
    - 数据库读写（由 database.repository 处理）
    - 特征级 Winsorization（由 handle_outliers 单独调用）
"""
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config.logging_config import get_logger

logger = get_logger(__name__)


# ── 常量 ─────────────────────────────────────────────────────

# 数值列全集（日线数据所有可能出现的数值字段）
_NUMERIC_COLUMNS = [
    'open', 'high', 'low', 'close',
    'pre_close', 'change', 'pct_chg',
    'vol', 'amount',
]

# 核心字段：这些字段含空值的行必须移除
_REQUIRED_COLUMNS = ['open', 'high', 'low', 'close', 'vol']

# 业务键
_BUSINESS_KEY = ['ts_code', 'trade_date']

# ts_code 合法格式：6位数字 + . + 交易所后缀
_TS_CODE_PATTERN = re.compile(r'^\d{6}\.(SZ|SH|BJ)$')

# 中文列名 → 英文标准列名映射（与 collector._normalize_akshare_columns 保持一致）
_CN_TO_EN_MAP = {
    '日期': 'trade_date',
    '开盘': 'open',
    '收盘': 'close',
    '最高': 'high',
    '最低': 'low',
    '成交量': 'vol',
    '成交额': 'amount',
    '涨跌额': 'change',
    '涨跌幅': 'pct_chg',
    '昨收': 'pre_close',
    '换手率': 'turnover_rate',
}

# trade_date 列名别名
_DATE_COLUMN_ALIASES = {'date', 'datetime', 'Date', 'DateTime'}


# ── 清洗报告 ─────────────────────────────────────────────────

@dataclass
class CleanReport:
    """单次清洗的统计报告"""
    stock_code: str = ''
    input_rows: int = 0
    null_rows: int = 0
    duplicate_rows: int = 0
    price_anomaly_rows: int = 0
    volume_anomaly_rows: int = 0
    output_rows: int = 0

    def summary(self) -> str:
        removed = self.input_rows - self.output_rows
        return (
            f"清洗报告 [{self.stock_code or 'ALL'}] "
            f"输入={self.input_rows} | "
            f"空值移除={self.null_rows} | "
            f"重复移除={self.duplicate_rows} | "
            f"价格异常={self.price_anomaly_rows} | "
            f"成交量异常={self.volume_anomaly_rows} | "
            f"总移除={removed} | "
            f"输出={self.output_rows}"
        )


# ── 清洗器 ───────────────────────────────────────────────────

class DataCleaner:
    """数据清洗器 — 职责明确的 9 步管线"""

    def __init__(self):
        self.last_report: CleanReport | None = None
        logger.info("数据清洗器初始化完成")

    # ── 公开接口 ──────────────────────────────────────────────

    def clean_stock_data(self, df, stock_code=None):
        """
        清洗股票数据（9 步管线）。

        Args:
            df: 原始数据 DataFrame
            stock_code: 股票代码（仅用于日志标识）

        Returns:
            DataFrame: 清洗后的数据
        """
        if df.empty:
            logger.warning(f"{stock_code}: 数据为空，跳过清洗")
            self.last_report = CleanReport(stock_code=stock_code or '')
            return df

        report = CleanReport(
            stock_code=stock_code or '',
            input_rows=len(df),
        )
        logger.info(f"{stock_code}: 开始清洗，原始数据 {report.input_rows} 条")

        # 工作副本，避免修改原始数据
        df = df.copy()

        # Step 1: 统一字段名称
        df = self._normalize_columns(df)

        # Step 2: 校验股票代码格式
        df = self._validate_ts_code(df)

        # Step 3: 统一交易日期格式
        df = self._normalize_trade_date(df)

        # Step 4: 数值类型强制转换
        df = self._convert_numeric(df)

        # Step 5: 移除核心字段含空值的行
        before = len(df)
        df = self._drop_null_rows(df)
        report.null_rows = before - len(df)

        # Step 6: 基于业务键去重
        before = len(df)
        df = self._drop_duplicates(df)
        report.duplicate_rows = before - len(df)

        # Step 7: 检测并移除价格异常
        before = len(df)
        df = self._detect_price_anomalies(df)
        report.price_anomaly_rows = before - len(df)

        # Step 8: 检测并移除成交量异常
        before = len(df)
        df = self._detect_volume_anomalies(df)
        report.volume_anomaly_rows = before - len(df)

        # Step 9: 排序
        df = self._sort_output(df)

        report.output_rows = len(df)
        self.last_report = report
        logger.info(report.summary())

        return df

    # ── 管线步骤 ──────────────────────────────────────────────

    def _normalize_columns(self, df):
        """Step 1: 统一字段名称（中文 → 英文，日期别名统一）"""
        # 中文列名映射
        rename_map = {k: v for k, v in _CN_TO_EN_MAP.items() if k in df.columns}
        if rename_map:
            df = df.rename(columns=rename_map)

        # 日期列别名统一为 trade_date
        if 'trade_date' not in df.columns:
            for alias in _DATE_COLUMN_ALIASES:
                if alias in df.columns:
                    df = df.rename(columns={alias: 'trade_date'})
                    break

        return df

    def _validate_ts_code(self, df):
        """Step 2: 校验 ts_code 格式，移除不合规行"""
        if 'ts_code' not in df.columns:
            return df

        before = len(df)
        valid_mask = df['ts_code'].astype(str).str.match(_TS_CODE_PATTERN, na=False)
        invalid_count = (~valid_mask).sum()

        if invalid_count > 0:
            df = df[valid_mask].reset_index(drop=True)
            logger.info(f"股票代码格式校验: 移除 {invalid_count} 条不合规 ts_code")

        return df

    def _normalize_trade_date(self, df):
        """Step 3: 统一 trade_date 为 datetime 类型，移除无法解析的行"""
        if 'trade_date' not in df.columns:
            return df

        df['trade_date'] = pd.to_datetime(df['trade_date'], errors='coerce')

        nat_count = df['trade_date'].isna().sum()
        if nat_count > 0:
            df = df.dropna(subset=['trade_date']).reset_index(drop=True)
            logger.info(f"日期格式校验: 移除 {nat_count} 条无法解析的 trade_date")

        return df

    def _convert_numeric(self, df):
        """Step 4: 数值列强制 float 转换"""
        for col in _NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                # 确保 float 类型（to_numeric 对纯整数保留 int 类型）
                if not pd.api.types.is_float_dtype(df[col]):
                    df[col] = df[col].astype(float)
        return df

    def _drop_null_rows(self, df):
        """Step 5: 移除核心字段含空值的行"""
        existing_required = [c for c in _REQUIRED_COLUMNS if c in df.columns]
        if not existing_required:
            return df
        return df.dropna(subset=existing_required).reset_index(drop=True)

    def _drop_duplicates(self, df):
        """Step 6: 基于业务键 [ts_code, trade_date] 去重，保留最后一条"""
        existing_key = [c for c in _BUSINESS_KEY if c in df.columns]
        if len(existing_key) == 2:
            return df.drop_duplicates(subset=existing_key, keep='last').reset_index(drop=True)
        # 回退：全行去重
        return df.drop_duplicates(keep='last').reset_index(drop=True)

    def _detect_price_anomalies(self, df):
        """
        Step 7: 检测并移除价格逻辑异常行。

        异常条件（任一中即移除）：
            - close <= 0
            - high < low
            - open > high（开盘价高于当日最高价）
            - open < low（开盘价低于当日最低价）
        """
        needed = {'open', 'high', 'low', 'close'}
        if not needed.issubset(df.columns):
            return df

        anomaly_mask = (
            (df['close'] <= 0) |
            (df['high'] < df['low']) |
            (df['open'] > df['high']) |
            (df['open'] < df['low'])
        )
        anomaly_count = anomaly_mask.sum()

        if anomaly_count > 0:
            df = df[~anomaly_mask].reset_index(drop=True)
            logger.info(f"价格异常检测: 移除 {anomaly_count} 条 "
                        f"(close<=0 / high<low / open越界)")

        return df

    def _detect_volume_anomalies(self, df):
        """Step 8: 检测并移除成交量异常行（vol < 0）"""
        if 'vol' not in df.columns:
            return df

        anomaly_mask = df['vol'] < 0
        anomaly_count = anomaly_mask.sum()

        if anomaly_count > 0:
            df = df[~anomaly_mask].reset_index(drop=True)
            logger.info(f"成交量异常检测: 移除 {anomaly_count} 条 (vol<0)")

        return df

    def _sort_output(self, df):
        """Step 9: 按业务键升序排列"""
        sort_cols = [c for c in _BUSINESS_KEY if c in df.columns]
        if sort_cols:
            df = df.sort_values(sort_cols).reset_index(drop=True)
        return df

    # ── 向后兼容方法（供旧代码 / fill 模式 / 特征后处理使用）──

    def remove_duplicates(self, df):
        """
        去除重复数据（全行去重，旧接口保留）。

        推荐使用 clean_stock_data 管线中的业务键去重。
        """
        before = len(df)
        df = df.drop_duplicates()
        after = len(df)
        if before != after:
            logger.info(f"去除{before - after}条重复数据")
        return df

    def handle_missing_values(self, df, strategy='drop'):
        """
        处理缺失值（旧接口保留）。

        Args:
            df: 数据 DataFrame
            strategy: 'drop' - 删除含空行 | 'fill' - 均值/众数填充

        Returns:
            DataFrame
        """
        missing_count = df.isnull().sum().sum()

        if missing_count == 0:
            logger.info("无缺失值")
            return df

        logger.info(f"发现{missing_count}个缺失值")

        if strategy == 'drop':
            df_cleaned = df.dropna()
            dropped = len(df) - len(df_cleaned)
            logger.info(f"删除{dropped}条含缺失值的记录")
            return df_cleaned

        elif strategy == 'fill':
            df = df.copy()
            numeric_cols = df.select_dtypes(include=[np.number]).columns
            for col in numeric_cols:
                if df[col].isnull().any():
                    df[col] = df[col].fillna(df[col].mean())

            categorical_cols = df.select_dtypes(exclude=[np.number]).columns
            for col in categorical_cols:
                if df[col].isnull().any() and not df[col].mode().empty:
                    df[col] = df[col].fillna(df[col].mode()[0])

            logger.info("使用均值/众数填充缺失值")
            return df

        return df

    def convert_dtypes(self, df):
        """转换数据类型（旧接口保留，列集合已扩展）。"""
        for col in _NUMERIC_COLUMNS:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                if not pd.api.types.is_float_dtype(df[col]):
                    df[col] = df[col].astype(float)

        date_columns = ['trade_date', 'date', 'datetime']
        for col in date_columns:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')

        logger.info("数据类型转换完成")
        return df

    def handle_outliers(self, df, columns=None, std_threshold=3):
        """
        异常值 Winsorization（旧接口保留，用于特征工程后的派生列截断）。

        仅对收益率等派生列做边界截断，不对原始价格做处理。

        Args:
            df: 数据 DataFrame
            columns: 需要检查的列，None 则自动选取收益率列
            std_threshold: 标准差阈值

        Returns:
            DataFrame
        """
        if columns is None:
            columns = [c for c in df.columns if c.startswith(('return_', 'future_', 'log_'))]
            if not columns:
                return df

        df = df.copy()
        for col in columns:
            if col not in df.columns:
                continue
            mean = df[col].mean()
            std = df[col].std()
            lower_bound = mean - std_threshold * std
            upper_bound = mean + std_threshold * std
            df[col] = df[col].clip(lower=lower_bound, upper=upper_bound)

        logger.info(f"异常值处理完成，共处理{len(columns)}列")
        return df