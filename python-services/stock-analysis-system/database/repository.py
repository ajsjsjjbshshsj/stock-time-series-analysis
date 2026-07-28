"""
数据访问层 (Repository)

所有对数据库表的读写操作集中在此模块。
外部模块通过 DatabaseConnector + repository 函数访问数据，
禁止在外部模块中拼接 SQL 或硬编码表名。
"""

import pandas as pd
from sqlalchemy import text

from config.logging_config import get_logger
from database.models import (
    TABLE_STOCK_BASIC,
    TABLE_STOCK_DAILY,
    TABLE_STOCK_FEATURES,
    TABLE_ANALYSIS_RESULT,
)

logger = get_logger(__name__)

_BATCH_SIZE = 5000


# ── 写入操作 ──────────────────────────────────────────────────


def save_daily_records(session, records, batch_size=_BATCH_SIZE):
    """
    批量写入日线数据到 stock_daily（INSERT IGNORE 去重）。

    Args:
        session: SQLAlchemy Session
        records: list[dict]，每条含 ts_code, trade_date, open, high, low, close, ...
        batch_size: 每批大小

    Returns:
        int: 写入行数
    """
    if not records:
        return 0

    stmt = text(
        f'INSERT IGNORE INTO `{TABLE_STOCK_DAILY}` '
        '(`ts_code`, `trade_date`, `open`, `high`, `low`, `close`, '
        '`pre_close`, `change`, `pct_chg`, `vol`, `amount`) '
        'VALUES (:ts_code, :trade_date, :open, :high, :low, :close, '
        ':pre_close, :change, :pct_chg, :vol, :amount)'
    )
    return _batch_execute(session, stmt, records, batch_size, TABLE_STOCK_DAILY)


def save_stock_basic(session, records):
    """
    写入股票基本信息到 stock_basic（INSERT IGNORE 去重）。

    Args:
        session: SQLAlchemy Session
        records: list[dict]，每条含 ts_code, symbol, name, area, industry, list_date

    Returns:
        int: 写入行数
    """
    if not records:
        return 0

    stmt = text(
        f'INSERT IGNORE INTO `{TABLE_STOCK_BASIC}` '
        '(ts_code, symbol, name, area, industry, list_date) '
        'VALUES (:ts_code, :symbol, :name, :area, :industry, :list_date)'
    )
    return _batch_execute(session, stmt, records, len(records), TABLE_STOCK_BASIC)


def save_features(session, records, db_columns, batch_size=_BATCH_SIZE):
    """
    批量写入特征数据到 stock_features（INSERT IGNORE 去重）。

    Args:
        session: SQLAlchemy Session
        records: list[dict]，每条含 ts_code, trade_date + 特征列
        db_columns: 特征列名列表（不含 ts_code, trade_date）
        batch_size: 每批大小

    Returns:
        int: 写入行数
    """
    if not records:
        return 0

    col_str = ', '.join([f'`{c}`' for c in db_columns])
    placeholders = ', '.join([f':{c}' for c in db_columns])
    stmt = text(
        f'INSERT IGNORE INTO `{TABLE_STOCK_FEATURES}` '
        f'(ts_code, trade_date, {col_str}) '
        f'VALUES (:ts_code, :trade_date, {placeholders})'
    )
    return _batch_execute(session, stmt, records, batch_size, TABLE_STOCK_FEATURES)


def save_analysis_result(session, record):
    """
    写入分析结果到 analysis_result（INSERT IGNORE 去重）。

    Args:
        session: SQLAlchemy Session
        record: dict，含 ts_code, analysis_date, analysis_type, result, prediction, confidence
    """
    stmt = text(
        f'INSERT IGNORE INTO `{TABLE_ANALYSIS_RESULT}` '
        '(ts_code, analysis_date, analysis_type, result, prediction, confidence) '
        'VALUES (:ts_code, :analysis_date, :analysis_type, :result, :prediction, :confidence)'
    )
    session.execute(stmt, [record])


# ── 读取操作 ──────────────────────────────────────────────────


def load_stock_basic_df(session):
    """
    读取 stock_basic 整表。

    Returns:
        DataFrame
    """
    return pd.read_sql_table(TABLE_STOCK_BASIC, session.bind)


def load_daily_panel(session, stock_codes=None, start_date=None, end_date=None):
    """
    从 stock_daily 加载日线面板数据。

    Args:
        session: SQLAlchemy Session
        stock_codes: 股票代码列表，None 则全部
        start_date: 起始日期（date 或 str）
        end_date: 结束日期（date 或 str）

    Returns:
        DataFrame
    """
    conditions = ['1=1']
    params = {}

    if stock_codes:
        placeholders = ', '.join([f':code_{i}' for i in range(len(stock_codes))])
        conditions.append(f'ts_code IN ({placeholders})')
        for i, code in enumerate(stock_codes):
            params[f'code_{i}'] = code

    if start_date:
        conditions.append('trade_date >= :start_date')
        params['start_date'] = pd.to_datetime(start_date).date() if isinstance(start_date, str) else start_date
    if end_date:
        conditions.append('trade_date <= :end_date')
        params['end_date'] = pd.to_datetime(end_date).date() if isinstance(end_date, str) else end_date

    where = ' AND '.join(conditions)
    query = f'SELECT * FROM `{TABLE_STOCK_DAILY}` WHERE {where} ORDER BY trade_date, ts_code'
    return pd.read_sql(text(query), session.bind, params=params)


def load_features_df(session, stock_codes=None, date_col='trade_date'):
    """
    从 stock_features 加载特征面板。

    Args:
        session: SQLAlchemy Session
        stock_codes: 股票代码列表，None 则全部
        date_col: 排序日期列名

    Returns:
        DataFrame
    """
    query = f'SELECT * FROM `{TABLE_STOCK_FEATURES}` WHERE 1=1'
    params = {}

    if stock_codes:
        placeholders = ', '.join([f':c{i}' for i in range(len(stock_codes))])
        query += f' AND ts_code IN ({placeholders})'
        for i, code in enumerate(stock_codes):
            params[f'c{i}'] = code

    query += f' ORDER BY {date_col}, ts_code'
    return pd.read_sql(text(query), session.bind, params=params)


def get_latest_trade_dates(session):
    """
    查询每只股票在 stock_daily 中的最新交易日期。

    Returns:
        dict: {ts_code: max_trade_date}
    """
    result = session.execute(text(
        f'SELECT ts_code, MAX(trade_date) as max_date '
        f'FROM `{TABLE_STOCK_DAILY}` GROUP BY ts_code'
    ))
    return {row[0]: row[1] for row in result}


def load_daily_with_names(session):
    """
    加载 stock_daily LEFT JOIN stock_basic（含股票名称），供 dashboard 使用。

    Returns:
        DataFrame
    """
    query = text(
        f'SELECT sd.ts_code, sd.trade_date, sd.`open`, sd.high, sd.low, sd.close, '
        f'sd.pre_close, sd.`change`, sd.pct_chg, sd.vol, sd.amount, '
        f'sc.name as 股票名称 '
        f'FROM `{TABLE_STOCK_DAILY}` sd '
        f'LEFT JOIN `{TABLE_STOCK_BASIC}` sc ON sd.ts_code = sc.ts_code '
        f'ORDER BY sd.trade_date, sd.ts_code'
    )
    return pd.read_sql(query, session.bind)


def load_daily_raw(session):
    """
    加载 stock_daily 原始数据（无 JOIN），供 dashboard 缓存刷新使用。

    Returns:
        DataFrame
    """
    query = text(
        f'SELECT sd.ts_code, sd.trade_date, sd.`open`, sd.high, sd.low, sd.close, '
        f'sd.pre_close, sd.`change`, sd.pct_chg, sd.vol, sd.amount '
        f'FROM `{TABLE_STOCK_DAILY}` sd '
        f'ORDER BY sd.trade_date, sd.ts_code'
    )
    return pd.read_sql(query, session.bind)


# ── 内部工具 ──────────────────────────────────────────────────


def _batch_execute(session, stmt, records, batch_size, table_name):
    """通用分批执行 INSERT 并记录进度。"""
    total = 0
    for i in range(0, len(records), batch_size):
        batch = records[i:i + batch_size]
        session.execute(stmt, batch)
        total += len(batch)
        if (i // batch_size + 1) % 20 == 0:
            logger.info(f"已写入 {total}/{len(records)} 条 → {table_name}")
    return total
