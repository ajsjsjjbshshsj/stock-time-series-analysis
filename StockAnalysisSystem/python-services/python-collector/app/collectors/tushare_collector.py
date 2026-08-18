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
from app.market_data.codes import to_ts_code
from app.market_data.tushare_client import TushareClient
from app.utils.logger import get_logger

logger = get_logger(__name__)


to_tushare_code = to_ts_code


class TushareCollector(BaseCollector):
    """Tushare 数据采集器"""

    def __init__(
        self,
        token: str | None = None,
        client: TushareClient | None = None,
    ):
        self.token = token or TUSHARE_TOKEN
        if client is None:
            ts.set_token(self.token)
            client = TushareClient(ts.pro_api())
        self.client = client
        # Temporary compatibility for date_utils.load_trade_calendar().
        self.pro = getattr(client, 'pro', None)
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

        df = self.client.daily(trade_date=trade_date_clean)
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
        ts_code = to_ts_code(ts_code)
        start_date = start_date.replace('-', '')
        end_date = end_date.replace('-', '')

        df = self.client.daily(
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
            df2 = self.client.daily_basic(
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
        df = self.client.stock_basic(
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

        df = self.client.trade_calendar(
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

    def collect_daily_basic(
        self,
        trade_date: str,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        trade_date_clean = trade_date.replace('-', '')
        params = {'trade_date': trade_date_clean}
        if ts_code:
            params['ts_code'] = to_ts_code(ts_code)
        df = self.client.daily_basic(**params)
        if df is None or df.empty:
            return pd.DataFrame()

        columns = [
            'ts_code', 'trade_date', 'turnover_rate',
            'pe', 'pe_ttm', 'pb', 'ps', 'total_mv',
        ]
        result = df.reindex(columns=columns).copy()
        result['trade_date'] = pd.to_datetime(result['trade_date']).dt.date
        return result

    def collect_index_constituents(
        self,
        index_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        as_of_clean = as_of_date.replace('-', '')
        df = self.client.index_weight(index_code=index_code)
        if df is None or df.empty:
            return pd.DataFrame()

        result = df.copy()
        result = result[result['trade_date'].astype(str) <= as_of_clean]
        result = result.sort_values('trade_date').drop_duplicates(
            'con_code', keep='last'
        )
        return pd.DataFrame({
            'group_type': 'index',
            'group_code': index_code,
            'ts_code': result['con_code'].map(to_ts_code),
            'as_of_date': pd.to_datetime(as_of_clean).date(),
            'weight': result.get('weight'),
            'source': self.source_name,
        }).reset_index(drop=True)

    def collect_industry_constituents(
        self,
        industry_code: str,
        as_of_date: str,
    ) -> pd.DataFrame:
        logger.warning("Tushare Collector 暂不提供行业成分股接口")
        return pd.DataFrame()
