"""基于 MySQL 成分股快照的行业/指数股票范围筛选。"""

from config.logging_config import get_logger

logger = get_logger(__name__)


def code_to_ts_code(code_6digit):
    """保留旧代码转换工具，供现有调用方兼容使用。"""
    code = str(code_6digit).strip().upper()
    if '.' in code:
        return code
    if code.startswith(('4', '8')):
        return f'{code}.BJ'
    if code.startswith(('6', '9')):
        return f'{code}.SH'
    return f'{code}.SZ'


def resolve_stock_codes(
    sectors=None,
    index_codes=None,
    use_tushare=False,
    market_repository=None,
):
    """从已落库的最新成分股快照解析股票代码。

    ``use_tushare`` 仅为旧调用签名兼容；分析端不再选择外部数据源。
    """
    if not sectors and not index_codes:
        return None
    if use_tushare:
        logger.info('use_tushare 已弃用；成分股统一从 MySQL 读取')

    if market_repository is not None:
        return _resolve_with_repository(
            market_repository, sectors or [], index_codes or []
        )

    from database.db_connector import DatabaseConnector
    from data_loader.market_data_repository import MarketDataRepository

    with DatabaseConnector() as db:
        with db.session_scope() as session:
            return _resolve_with_repository(
                MarketDataRepository(session), sectors or [], index_codes or []
            )


def _resolve_with_repository(market_repository, sectors, index_codes):
    all_codes = set()

    for sector_name in sectors:
        frame = market_repository.get_industry_constituents(sector_name)
        if frame.empty or 'ts_code' not in frame.columns:
            logger.warning('行业 [%s] 没有已落库的成分股快照', sector_name)
            continue
        all_codes.update(frame['ts_code'].dropna().astype(str))

    for index_code in index_codes:
        frame = market_repository.get_index_constituents(index_code)
        if frame.empty or 'ts_code' not in frame.columns:
            logger.warning('指数 [%s] 没有已落库的成分股快照', index_code)
            continue
        all_codes.update(frame['ts_code'].dropna().astype(str))

    if not all_codes:
        logger.error('板块/指数筛选未找到成分股，不使用全市场兜底')
        return []
    result = sorted(all_codes)
    logger.info('板块/指数筛选共获得 %s 只股票', len(result))
    return result
