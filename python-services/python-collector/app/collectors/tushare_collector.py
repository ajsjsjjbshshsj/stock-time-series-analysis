"""
Tushare 数据采集器

职责：
    1. 初始化 Tushare 客户端
    2. 调用 Tushare 接口
    3. 处理接口字段差异
    4. 返回统一字段的数据
    5. 处理空结果和接口异常

不负责：
    - 数据库写入
    - 任务状态管理
    - 技术指标计算
"""

import pandas as pd
import tushare as ts

from app.collectors.base_collector import BaseCollector
from app.config import TUSHARE_TOKEN
from app.utils.logger import get_logger

logger = get_logger(__name__)


def to_tushare_code(code: str) -> str:
    """
    将 6 位股票代码转换为 Tushare 格式。

    规则：
        6/9 开头 → .SH（上海）
        其他     → .SZ（深圳）
        已带后缀 → 直接返回大写
    """
    code = str(code).strip()
    if '.' in code:
        return code.upper()
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    return f"{code}.SZ"


class TushareCollector(BaseCollector):
    """Tushare 数据采集器"""

    def __init__(self, token: str | None = None):
        self.token = token or TUSHARE_TOKEN
        ts.set_token(self.token)
        self.pro = ts.pro_api()
        logger.info("TushareCollector 初始化完成")

    @property
    def source_name(self) -> str:
        return 'tushare'

    def collect_daily(self, trade_date: str) -> pd.DataFrame:
        """
        采集指定交易日全市场行情（使用 Tushare daily 接口按日期批量拉取）。

        Args:
            trade_date: YYYYMMDD

        Returns:
            统一格式 DataFrame
        """
        trade_date_clean = trade_date.replace('-', '')

        df = self.pro.daily(trade_date=trade_date_clean)
        if df is None or df.empty:
            logger.info(f"Tushare daily({trade_date_clean}): 无数据")
            return pd.DataFrame()

        # 选取标准列
        columns = [
            'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        ]
        available = [c for c in columns if c in df.columns]
        df = df[available].copy()

        # 日期转换
        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date

        logger.info(f"Tushare daily({trade_date_clean}): {len(df)} 条")
        return df

    def collect_daily_single(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """
        采集单只股票日线数据（合并 daily + daily_basic）。
        """
        ts_code = to_tushare_code(ts_code)
        start_date = start_date.replace('-', '')
        end_date = end_date.replace('-', '')

        df = self.pro.daily(
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        df = df[[
            'ts_code', 'trade_date', 'open', 'high', 'low', 'close',
            'pre_close', 'change', 'pct_chg', 'vol', 'amount',
        ]].copy()

        # 合并 daily_basic（估值、换手率等）
        try:
            df2 = self.pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
            if df2 is not None and not df2.empty:
                df2 = df2[[
                    'ts_code', 'trade_date', 'turnover_rate',
                    'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
                ]]
                df = pd.merge(df, df2, on=['ts_code', 'trade_date'], how='left')
        except Exception as e:
            logger.warning(f"Tushare daily_basic 合并失败 ({ts_code}): {e}")

        df['trade_date'] = pd.to_datetime(df['trade_date']).dt.date
        df = df.sort_values('trade_date')
        logger.info(f"Tushare {ts_code} 日线数据: {len(df)} 条")
        return df

    def collect_basic(self) -> pd.DataFrame:
        """获取股票基本信息。"""
        df = self.pro.stock_basic(
            exchange='',
            list_status='L',
            fields='ts_code,symbol,name,area,industry,list_date',
        )
        if df is None or df.empty:
            logger.warning("Tushare 获取股票基本信息为空")
            return pd.DataFrame()

        logger.info(f"Tushare 获取股票基本信息: {len(df)} 只")
        return df

    def collect_trade_calendar(
        self,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        """获取交易日历。"""
        start_date = start_date.replace('-', '')
        end_date = end_date.replace('-', '')

        df = self.pro.trade_cal(
            exchange='SSE',
            start_date=start_date,
            end_date=end_date,
            fields='cal_date,is_open',
        )
        if df is None or df.empty:
            logger.warning("Tushare 获取交易日历为空")
            return pd.DataFrame()

        logger.info(f"Tushare 交易日历: {start_date}~{end_date}, {len(df)} 天")
        return df
