"""从 MySQL 读取少量股票，快速验证分析端数据边界。"""

from datetime import datetime

from config.logging_config import logger
from data_loader.collector import DataCollector


TEST_STOCKS = [
    '000001.SZ',
    '600000.SH',
    '000002.SZ',
    '600519.SH',
    '300750.SZ',
]
START_DATE = '20260101'
END_DATE = datetime.now().strftime('%Y%m%d')


if __name__ == '__main__':
    logger.info(
        '小批量数据库读取验证: %s 只股票, %s ~ %s',
        len(TEST_STOCKS), START_DATE, END_DATE,
    )
    results = DataCollector().collect_daily_data(
        TEST_STOCKS, START_DATE, END_DATE
    )
    for code in TEST_STOCKS:
        frame = results.get(code)
        logger.info('%s: %s 条', code, 0 if frame is None else len(frame))
