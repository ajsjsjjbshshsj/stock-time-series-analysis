"""旧分析采集接口的 MySQL 兼容门面。

外部行情采集已统一交给 ``python-collector``。本模块只保留旧调用签名，
并把读取请求转给分析应用的 ``MarketDataRepository``。
"""

from datetime import datetime

import pandas as pd

from config.logging_config import get_logger
from config.settings import DATA_COLLECTION

logger = get_logger(__name__)


class DataCollector:
    """保留旧方法名和返回结构的数据库读取门面。"""

    def __init__(self, use_tushare=True, repository=None):
        self.use_tushare = use_tushare
        self.repository = repository
        logger.info(
            'DataCollector 已使用 MySQL；数据源选择由 python-collector 负责'
        )

    def fetch_stock_list(self):
        """从本地 ``stock_basic`` 返回股票列表。"""
        frame = self._call_repository('fetch_stock_list')
        if frame.empty:
            logger.warning('数据库中的股票列表为空')
        return frame

    def fetch_single(self, code, start_date, end_date):
        """按旧签名读取一只股票的日线和每日指标。"""
        return self._call_repository(
            'fetch_single', code, start_date, end_date
        )

    def collect_daily_data(
        self,
        stock_codes,
        start_date=None,
        end_date=None,
        delay=None,
    ):
        """批量读取并返回旧的 ``{ts_code: DataFrame}`` 结构。"""
        del delay  # 数据库读取不需要外部接口限速参数
        if start_date is None:
            start_date = DATA_COLLECTION['默认开始日期']
        if end_date is None:
            end_date = datetime.now().strftime('%Y%m%d')

        panel = self._call_repository(
            'load_daily_panel', stock_codes, start_date, end_date
        )
        if panel is None or panel.empty or 'ts_code' not in panel.columns:
            return {}
        return {
            code: frame.reset_index(drop=True)
            for code, frame in panel.groupby('ts_code', sort=False)
        }

    def collect_all_stocks(
        self,
        stock_codes=None,
        start_date=None,
        end_date=None,
    ):
        """读取全部或指定股票，保持旧批量方法行为。"""
        if stock_codes is None:
            stock_codes = self._get_stock_codes()
            if not stock_codes:
                logger.warning('数据库中没有可用股票代码')
                return {}
        return self.collect_daily_data(stock_codes, start_date, end_date)

    def _get_stock_codes(self):
        frame = self.fetch_stock_list()
        if frame.empty or 'ts_code' not in frame.columns:
            return []
        return frame['ts_code'].dropna().drop_duplicates().tolist()

    def _call_repository(self, method_name, *args):
        if self.repository is not None:
            return getattr(self.repository, method_name)(*args)

        # 默认按每次兼容调用创建短生命周期会话，旧调用方无需管理连接。
        from database.db_connector import DatabaseConnector
        from data_loader.market_data_repository import MarketDataRepository

        with DatabaseConnector() as db:
            with db.session_scope() as session:
                repository = MarketDataRepository(session)
                result = getattr(repository, method_name)(*args)
                if isinstance(result, pd.DataFrame):
                    return result.copy()
                return result
