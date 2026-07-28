"""
小批量数据采集测试 - 逐条写入定位失败记录
"""
import time
import pandas as pd
from datetime import datetime
from config.logging_config import logger
from database.db_connector import DatabaseConnector
from data_loader.collector import DataCollector
from sqlalchemy import text

# 选 5 只不同市场的股票做测试
TEST_STOCKS = [
    '000001.SZ',  # 平安银行（深市）
    '600000.SH',  # 浦发银行（沪市）
    '000002.SZ',  # 万科A
    '600519.SH',  # 贵州茅台
    '300750.SZ',  # 宁德时代（创业板）
]

START_DATE = '20260101'
END_DATE = datetime.now().strftime('%Y%m%d')

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info(f"小批量采集测试: 5 只股票, {START_DATE} ~ {END_DATE}")
    logger.info("=" * 60)

    collector = DataCollector(use_tushare=True)  # Tushare
    db = DatabaseConnector()
    session = db.get_session()

    all_records = []

    for idx, code in enumerate(TEST_STOCKS, 1):
        try:
            symbol = code.split('.')[0]
            logger.info(f"[{idx}/{len(TEST_STOCKS)}] 采集 {code} ({symbol})...")
            df = collector.api.get_stock_zh_a_hist(symbol, start_date=START_DATE, end_date=END_DATE)

            if df.empty:
                logger.warning(f"  {code} 无数据")
                continue

            # 标准化列名
            df.rename(columns={
                '日期': 'trade_date', '开盘': 'open', '收盘': 'close',
                '最高': 'high', '最低': 'low', '成交量': 'vol', '成交额': 'amount',
                '涨跌额': 'change', '涨跌幅': 'pct_chg'
            }, inplace=True)

            # 反推昨收价
            if 'close' in df.columns and 'pct_chg' in df.columns:
                mask = df['pct_chg'].notna() & (df['pct_chg'] != 0)
                df.loc[mask, 'pre_close'] = (df.loc[mask, 'close'] / (1 + df.loc[mask, 'pct_chg'] / 100)).round(2)

            for _, row in df.iterrows():
                trade_date = pd.to_datetime(row['trade_date'])
                all_records.append({
                    'ts_code': code,
                    'trade_date': trade_date,
                    'open': float(row.get('open', 0)),
                    'high': float(row.get('high', 0)),
                    'low': float(row.get('low', 0)),
                    'close': float(row.get('close', 0)),
                    'pre_close': float(row.get('pre_close', 0)) if 'pre_close' in row else None,
                    'change': float(row.get('change', 0)) if 'change' in row else None,
                    'pct_chg': float(row.get('pct_chg', 0)) if 'pct_chg' in row else None,
                    'vol': float(row.get('vol', 0)),
                    'amount': float(row.get('amount', 0)) if 'amount' in row else None,
                })
            logger.info(f"  {code}: {len(df)} 条记录")
            time.sleep(0.5)

        except Exception as e:
            logger.error(f"  {code} 采集失败: {e}")
            continue

    # 逐条写入数据库，定位失败记录
    stmt = text(
        'INSERT IGNORE INTO stock_daily '
        '(`ts_code`, `trade_date`, `open`, `high`, `low`, `close`, '
        '`pre_close`, `change`, `pct_chg`, `vol`, `amount`) '
        'VALUES (:ts_code, :trade_date, :open, :high, :low, :close, '
        ':pre_close, :change, :pct_chg, :vol, :amount)'
    )

    success = 0
    skipped = 0

    if all_records:
        logger.info(f"开始逐条写入，共 {len(all_records)} 条记录...")
        for i, record in enumerate(all_records):
            try:
                session.execute(stmt, record)
                session.commit()
                success += 1
            except Exception as e:
                session.rollback()
                skipped += 1
                logger.error(f"  第{i}条写入失败: ts_code={record['ts_code']}, date={record['trade_date']} | {e}")

        logger.info(f"写入完成: 成功 {success} 条, 跳过 {skipped} 条")
    else:
        logger.warning("无数据可写入")

    session.close()
    db.close()
