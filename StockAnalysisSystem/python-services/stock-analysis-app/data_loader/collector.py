"""
数据采集流程控制

DataCollector 负责：
- 采集任务调度（批量 / 单股）
- 接口调用失败重试（指数退避）
- 空数据判断与日志
- 采集开始、结束、数量和耗时统计

不负责：
- 数据库读写（由 database.repository 处理）
- 技术指标计算（由 data_processor.feature_engineer 处理）
"""

import time
import pandas as pd
from datetime import datetime
from config.settings import DATA_COLLECTION
from config.logging_config import get_logger

logger = get_logger(__name__)


class DataCollector:
    """数据采集器 — 负责采集流程和任务控制"""

    def __init__(self, use_tushare=True):
        """
        初始化数据采集器

        Args:
            use_tushare: 是否使用 Tushare，False 则使用 Akshare
        """
        self.use_tushare = use_tushare
        if use_tushare:
            from data_loader.tushare_api import TushareAPI
            self.api = TushareAPI()
        else:
            from data_loader.tushare_api import AkshareAPI
            self.api = AkshareAPI()
        logger.info(f"数据采集器初始化完成，数据源: {'Tushare' if use_tushare else 'Akshare'}")

    # ── 公开接口 ──────────────────────────────────────────────

    def fetch_stock_list(self):
        """
        从数据源获取股票列表。

        Returns:
            DataFrame: 股票列表
                       Tushare: ts_code, symbol, name, area, industry, list_date
                       Akshare: 代码, 名称
        """
        logger.info("正在获取股票列表...")
        if self.use_tushare:
            df = self.api.get_stock_basic()
        else:
            df = self.api.get_stock_info()

        if df.empty:
            logger.warning("获取到的股票列表为空")
        else:
            logger.info(f"获取到 {len(df)} 只股票")
        return df

    def fetch_single(self, code, start_date, end_date):
        """
        采集单只股票日线数据（带重试和列名标准化）。

        Args:
            code: 股票代码（Tushare 格式如 000001.SZ，或纯 6 位）
            start_date: 开始日期 (YYYYMMDD)
            end_date: 结束日期 (YYYYMMDD)

        Returns:
            DataFrame: 日线数据（成功，可能为空）
            None: 采集失败（全部重试耗尽）
        """
        return self._fetch_with_retry(code, start_date, end_date)

    def collect_daily_data(self, stock_codes, start_date=None, end_date=None, delay=None):
        """
        批量采集多只股票日线数据。

        Args:
            stock_codes: 股票代码列表
            start_date: 开始日期 (YYYYMMDD)，None 则使用默认配置
            end_date: 结束日期 (YYYYMMDD)，None 则为当前日期
            delay: 请求间隔秒数，None 则使用配置

        Returns:
            dict: {股票代码: DataFrame}，仅含成功且非空的结果
        """
        if start_date is None:
            start_date = DATA_COLLECTION['默认开始日期']
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')
        if delay is None:
            delay = DATA_COLLECTION['request_interval']

        total = len(stock_codes)
        logger.info(
            f"采集开始: {total} 只股票, "
            f"日期 {start_date}~{end_date}, "
            f"数据源 {'Tushare' if self.use_tushare else 'Akshare'}"
        )

        t0 = time.time()
        results = {}
        fail_count = 0
        empty_count = 0

        for idx, code in enumerate(stock_codes, 1):
            df = self.fetch_single(code, start_date, end_date)

            if df is None:
                fail_count += 1
            elif df.empty:
                empty_count += 1
            else:
                results[code] = df

            # 进度日志
            if idx <= 3 or idx % 100 == 0:
                logger.info(
                    f"进度: {idx}/{total} | "
                    f"成功={len(results)}, 空数据={empty_count}, 失败={fail_count}"
                )

            # 请求间隔
            if idx < total:
                time.sleep(delay)

        elapsed = time.time() - t0
        logger.info(
            f"采集结束: 成功={len(results)}/{total}, "
            f"空数据={empty_count}, 失败={fail_count}, "
            f"耗时 {elapsed:.1f}s"
        )

        if not results:
            logger.warning("采集结果为空，请检查网络连接或数据源配置")

        return results

    def collect_all_stocks(self, stock_codes=None, start_date=None, end_date=None):
        """
        采集多只股票数据（兼容旧接口）。

        Args:
            stock_codes: 股票代码列表，None 则自动获取股票列表
            start_date: 开始日期
            end_date: 结束日期

        Returns:
            dict: {股票代码: DataFrame}
        """
        if stock_codes is None:
            stock_codes = self._get_stock_codes()
            if not stock_codes:
                logger.error("无法获取股票代码列表")
                return {}

        return self.collect_daily_data(stock_codes, start_date, end_date)

    # ── 内部实现 ──────────────────────────────────────────────

    def _fetch_with_retry(self, code, start_date, end_date):
        """
        带重试的单股数据采集（指数退避）。

        Returns:
            DataFrame: 成功时返回（可能为空 DataFrame）
            None: 全部重试失败
        """
        retry_times = DATA_COLLECTION['retry_times']
        retry_delay = DATA_COLLECTION['retry_delay']

        for attempt in range(1, retry_times + 1):
            try:
                df = self._call_api(code, start_date, end_date)

                if df is None or df.empty:
                    if attempt < retry_times:
                        wait = retry_delay * (2 ** (attempt - 1))
                        logger.debug(
                            f"{code} 返回空数据，第 {attempt}/{retry_times} 次重试，"
                            f"等待 {wait:.1f}s"
                        )
                        time.sleep(wait)
                        continue
                    logger.debug(f"{code} 无数据（已重试 {retry_times} 次）")
                    return pd.DataFrame()

                return df

            except Exception as e:
                if attempt < retry_times:
                    wait = retry_delay * (2 ** (attempt - 1))
                    logger.warning(
                        f"{code} 第 {attempt}/{retry_times} 次调用失败: {e}，"
                        f"等待 {wait:.1f}s 后重试"
                    )
                    time.sleep(wait)
                else:
                    logger.error(f"{code} 采集失败，已重试 {retry_times} 次: {e}")
                    return None

        return None

    def _call_api(self, code, start_date, end_date):
        """
        调用底层 API 获取单股日线数据，Akshare 自动标准化列名。

        Returns:
            DataFrame
        """
        if self.use_tushare:
            return self.api.get_daily_data(code, start_date, end_date)
        else:
            symbol = code.split('.')[0]
            df = self.api.get_stock_zh_a_hist(
                symbol, start_date=start_date, end_date=end_date
            )
            if df is None or df.empty:
                return pd.DataFrame()
            return _normalize_akshare_columns(df)

    def _get_stock_codes(self):
        """
        获取股票代码列表（自动获取）。

        Returns:
            list: 股票代码列表
        """
        df = self.fetch_stock_list()
        if df.empty:
            return []

        if self.use_tushare:
            return df['ts_code'].tolist() if 'ts_code' in df.columns else []
        else:
            return df['代码'].tolist() if '代码' in df.columns else []


def _normalize_akshare_columns(df):
    """
    将 Akshare 返回的中文列名标准化为英文。

    Args:
        df: Akshare 原始 DataFrame

    Returns:
        DataFrame: 列名标准化后的数据（副本）
    """
    df = df.copy()
    rename_map = {
        '日期': 'trade_date',
        '开盘': 'open',
        '收盘': 'close',
        '最高': 'high',
        '最低': 'low',
        '成交量': 'vol',
        '成交额': 'amount',
        '涨跌额': 'change',
        '涨跌幅': 'pct_chg',
    }
    df = df.rename(columns=rename_map)

    # Akshare 无昨收字段，从收盘价和涨跌幅反推
    if 'close' in df.columns and 'pct_chg' in df.columns:
        mask = df['pct_chg'].notna() & (df['pct_chg'] != 0)
        df.loc[mask, 'pre_close'] = (
            df.loc[mask, 'close'] / (1 + df.loc[mask, 'pct_chg'] / 100)
        ).round(2)

    return df