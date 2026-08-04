# -*- coding: utf-8 -*-
"""
板块/指数筛选工具

支持按行业板块名称或指数代码筛选股票范围。
"""
from config.logging_config import get_logger
logger = get_logger(__name__)


def code_to_ts_code(code_6digit):
    """
    将6位股票代码转为 ts_code 格式（带交易所后缀）。

    规则:
        6xxxxx -> .SH (上海证券交易所)
        0xxxxx / 3xxxxx -> .SZ (深圳/创业板)
        其他 -> .BJ (北交所)

    参数:
        code_6digit: 6位纯数字股票代码
    返回:
        str: 带后缀的 ts_code，如 "000001.SZ"
    """
    code = str(code_6digit).strip()
    if code.startswith(('6', '9')):
        return f"{code}.SH"
    elif code.startswith(('0', '3')):
        return f"{code}.SZ"
    else:
        return f"{code}.BJ"


def resolve_stock_codes(sectors=None, index_codes=None, use_tushare=False):
    """
    根据行业板块或指数代码解析目标股票代码列表。

    参数:
        sectors: 行业板块名称列表，如 ["银行", "医药", "电子"]
        index_codes: 指数代码列表，如 ["000300", "000905"]
    返回:
        list[str] | None: ts_code 格式的股票代码列表；
                          两个参数都为 None 时返回 None（表示不限，全市场）。
    """
    if not sectors and not index_codes:
        return None

    all_codes = set()

    # 行业板块筛选
    if sectors:
        from data_loader.tushare_api import AkshareAPI
        api = AkshareAPI()
        for sector_name in sectors:
            try:
                logger.info(f"正在获取行业板块 [{sector_name}] 成分股...")
                df = api.get_industry_stocks(sector_name)
                if df.empty:
                    logger.warning(f"行业板块 [{sector_name}] 无成分股数据")
                    continue
                # 列名适配
                code_col = None
                for col in ('代码', 'code', 'symbol'):
                    if col in df.columns:
                        code_col = col
                        break
                if code_col is None:
                    logger.warning(f"行业板块 [{sector_name}] 返回数据无 '代码' 列，列名: {list(df.columns)}")
                    continue
                for code in df[code_col]:
                    all_codes.add(code_to_ts_code(code))
                logger.info(f"行业 [{sector_name}] 获取 {len(df)} 只成分股")
            except Exception as e:
                logger.error(f"获取行业 [{sector_name}] 成分股失败: {e}")

    # 指数成分股筛选
    if index_codes:
        if use_tushare:
            from data_loader.tushare_api import TushareAPI
            api = TushareAPI()
        else:
            from data_loader.tushare_api import AkshareAPI
            api = AkshareAPI()
        for idx_code in index_codes:
            try:
                logger.info(f"正在获取指数 [{idx_code}] 成分股...")
                df = api.get_index_constituents(idx_code)
                if df.empty:
                    logger.warning(f"指数 [{idx_code}] 无成分股数据")
                    continue
                # 列名适配
                code_col = None
                for col in ('con_code', '品种代码', '成分券代码', 'code', 'symbol'):
                    if col in df.columns:
                        code_col = col
                        break
                if code_col is None:
                    logger.warning(f"指数 [{idx_code}] 返回数据无代码列，列名: {list(df.columns)}")
                    continue
                for code in df[code_col]:
                    code = str(code).strip()
                    all_codes.add(code if '.' in code else code_to_ts_code(code))
                logger.info(f"指数 [{idx_code}] 获取 {len(df)} 只成分股")
            except Exception as e:
                logger.error(f"获取指数 [{idx_code}] 成分股失败: {e}")

    if not all_codes:
        logger.error("板块/指数筛选未获取到任何股票，已停止使用全市场兜底")
        return []

    result = sorted(all_codes)
    logger.info(f"板块/指数筛选共获取 {len(result)} 只股票（去重后）")
    return result
