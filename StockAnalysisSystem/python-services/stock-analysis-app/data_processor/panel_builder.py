# -*- coding: utf-8 -*-
"""
面板数据构建器 (Panel Data Builder)

将多只股票的独立数据合并为横截面面板数据（每行=一只股票一天），
支持从数据库读取已有数据 + 增量采集最新数据。
"""

import os
import math
import hashlib
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config.logging_config import get_logger
logger = get_logger(__name__)
from database.db_connector import DatabaseConnector
from database import repository
from data_loader.collector import DataCollector
from data_processor.feature_engineer import (
    FeatureEngineer, validate_and_clean,
    FEAT_FUTURE_RETURN_1D, FEAT_FUTURE_RETURN_5D, FEAT_RETURN_1D,
    FEATURE_VERSION as _FEAT_VERSION,
)


def filter_stock_list(basic_df):
    """
    筛选沪深市场股票，排除北交所和ST股。

    参数:
        basic_df: 包含 ts_code, name 列的 DataFrame
    返回:
        DataFrame: 过滤后的 DataFrame
    """
    if basic_df.empty:
        return basic_df

    # 1. 排除北交所（代码以 8 或 4 开头）
    mask_bse = ~basic_df['ts_code'].str.match(r'^(8|4)')

    # 2. 排除ST股（名称包含 ST 或 st）
    if 'name' in basic_df.columns:
        mask_st = ~basic_df['name'].str.contains(r'ST|st', na=False)
    else:
        mask_st = True

    before = len(basic_df)
    filtered = basic_df[mask_bse & mask_st].reset_index(drop=True)
    logger.info(f"股票筛选: {before} 只 -> {len(filtered)} 只 (排除北交所 {before - basic_df[mask_bse].shape[0]} 只, ST股 {basic_df[mask_bse].shape[0] - len(filtered)} 只)")
    return filtered


def save_raw_panel(panel_df, path):
    """按旧路径和字段形状保存原始面板缓存。"""
    raw_save = panel_df.copy()
    if 'turnover_rate' not in raw_save.columns:
        raw_save['turnover_rate'] = pd.NA
    raw_save.to_parquet(path, engine='pyarrow', index=False)


def restore_turnover_rate_from_cache(panel_df, raw_cache_path):
    """仅为数据库缺失值补入旧缓存换手率，不覆盖数据库非空值。"""
    if not os.path.exists(raw_cache_path):
        return panel_df

    try:
        cached = pd.read_parquet(
            raw_cache_path,
            engine='pyarrow',
            columns=['ts_code', 'trade_date', 'turnover_rate'],
        )
        cached['trade_date'] = pd.to_datetime(cached['trade_date'])
        cached = cached.drop_duplicates(['ts_code', 'trade_date'], keep='last')
        cached = cached.rename(columns={
            'turnover_rate': '_cached_turnover_rate'
        })

        result = panel_df.copy()
        result['trade_date'] = pd.to_datetime(result['trade_date'])
        if 'turnover_rate' not in result.columns:
            result['turnover_rate'] = pd.NA
        result = result.merge(
            cached, on=['ts_code', 'trade_date'], how='left'
        )
        missing = result['turnover_rate'].isna()
        recovered = missing & result['_cached_turnover_rate'].notna()
        result.loc[missing, 'turnover_rate'] = result.loc[
            missing, '_cached_turnover_rate'
        ]
        result = result.drop(columns=['_cached_turnover_rate'])
        if recovered.any():
            logger.warning(
                'daily_basic 尚未补齐，临时从 raw_panel.parquet 恢复 %s 行换手率',
                int(recovered.sum()),
            )
        return result
    except Exception as exc:
        logger.warning('读取换手率兼容缓存失败: %s', exc)
        return panel_df


def load_all_stock_data_from_db(start_date=None, end_date=None,
                                sectors=None, index_codes=None, use_tushare=False,
                                stock_codes=None, *, strict_validation=False):
    """
    从数据库加载所有股票的日线数据。

    参数:
        start_date: 开始日期，格式 YYYYMMDD，默认20200101
        end_date: 结束日期，格式 YYYYMMDD，默认最新
        sectors: 行业板块名称列表，如 ["银行", "医药"]
        index_codes: 指数代码列表，如 ["000300"]
    返回:
        DataFrame: 面板数据，包含所有股票的日线和基础技术指标
    """
    try:
        with DatabaseConnector() as db:
            with db.session_scope() as session:

                # 获取股票列表
                basic_df = repository.load_stock_basic_df(session)
                if basic_df.empty:
                    logger.warning("数据库中无股票列表")
                    return pd.DataFrame()

                # 筛选沪深市场，排除北交所和ST
                basic_df = filter_stock_list(basic_df)

                if stock_codes is not None:
                    basic_df = basic_df[basic_df['ts_code'].isin(stock_codes)]
                    logger.info(f"指定股票列表筛选后: {len(basic_df)} 只股票")

                # 应用板块/指数筛选
                if sectors or index_codes:
                    from data_processor.stock_filter import resolve_stock_codes
                    filtered_codes = resolve_stock_codes(
                        sectors=sectors, index_codes=index_codes, use_tushare=use_tushare
                    )
                    if filtered_codes is not None:
                        basic_df = basic_df[basic_df['ts_code'].isin(filtered_codes)]
                        logger.info(f"板块/指数筛选后: {len(basic_df)} 只股票")

                requested_codes = list(stock_codes) if stock_codes is not None else basic_df['ts_code'].tolist()
                stock_codes = basic_df['ts_code'].tolist()
                code_name_map = dict(zip(basic_df['ts_code'], basic_df['name']))
                logger.info(f"从数据库获取 {len(stock_codes)} 只股票")
                if not stock_codes:
                    logger.warning("筛选后股票列表为空")
                    return pd.DataFrame()

                # 获取日线数据
                panel_df = repository.load_daily_panel(session, stock_codes, start_date, end_date)

                if panel_df.empty:
                    logger.warning("数据库中无日线数据")
                    return pd.DataFrame()

                # 添加股票名称
                panel_df['股票名称'] = panel_df['ts_code'].map(code_name_map)

                logger.info(f"加载面板数据: {len(panel_df)} 行, {panel_df['ts_code'].nunique()} 只股票")

                # 防御：去除重复列名
                if panel_df.columns.duplicated().any():
                    panel_df = panel_df.loc[:, ~panel_df.columns.duplicated()]

                # DB JOIN 是主来源；旧缓存只填补尚未历史补采的空值。
                CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'models', 'traditional_features')
                raw_cache = os.path.join(CACHE_DIR, 'raw_panel.parquet')
                if not strict_validation:
                    panel_df = restore_turnover_rate_from_cache(panel_df, raw_cache)
                panel_df.attrs['requested_codes'] = sorted(requested_codes)
                panel_df.attrs['source_mode'] = 'strict_database' if strict_validation else 'legacy_compatibility'

                return panel_df

    except Exception as e:
        if strict_validation:
            raise ValueError('Strict market database loading failed') from None
        logger.error(f"从数据库加载数据失败: {e}")
        return pd.DataFrame()


def load_stock_features_from_db(stock_codes=None, date_col='trade_date'):
    """
    从 stock_features 表批量加载传统管线特征（缓存复用）。

    参数:
        stock_codes: 股票代码列表，None 则加载全部
        date_col: 日期列名

    返回:
        DataFrame: 特征面板数据（含 ts_code, trade_date 及所有特征列）
    """
    try:
        with DatabaseConnector() as db:
            with db.session_scope() as session:
                df = repository.load_features_df(session, stock_codes, date_col)
                if df.empty:
                    logger.warning("数据库中无特征数据")
                    return pd.DataFrame()
                logger.info(f"从 stock_features 加载特征: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
                return df
    except Exception as e:
        logger.error(f"加载特征失败: {e}")
        return pd.DataFrame()


def compute_and_save_traditional_features_batch(stock_codes=None, use_tushare=False,
                                                 n_workers=None):
    """
    批量计算传统管线特征并保存到 stock_features 表（增量模式）。

    流程：
        1. 从 stock_daily 读取已有日线数据
        2. 逐股计算 FeatureEngineer 全量特征
        3. 批量写入 stock_features 表（INSERT IGNORE 去重）

    参数:
        stock_codes: 股票代码列表，None 则全部
        use_tushare: 数据源
        n_workers: 并行进程数

    返回:
        DataFrame: 特征面板数据
    """
    try:
        with DatabaseConnector() as db:
            with db.session_scope() as session:
                # 1. 加载日线数据
                panel_df = load_all_stock_data_from_db(
                    stock_codes=stock_codes, sectors=None, index_codes=None,
                    use_tushare=use_tushare
                )
                if panel_df.empty:
                    logger.error("日线数据为空，请先采集数据")
                    return pd.DataFrame()

                logger.info(f"开始批量计算特征: {panel_df['ts_code'].nunique()} 只股票, {len(panel_df)} 行")

                # 2. 逐股计算特征
                engineer = FeatureEngineer()
                codes = panel_df['ts_code'].unique()
                all_records = []
                success_count = 0
                fail_count = 0

                for idx, code in enumerate(codes, 1):
                    stock_df = panel_df[panel_df['ts_code'] == code].copy()
                    stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)

                    if len(stock_df) < 60:
                        continue

                    try:
                        featured = engineer.calculate_all_features(stock_df)

                        # 构建数据库记录
                        # 新 feature_engineer v2.0 输出 snake_case，直接使用
                        available = {
                            df_col: db_col
                            for df_col, db_col in FEATURE_COL_MAP.items()
                            if df_col in featured.columns
                        }

                        for _, row in featured.iterrows():
                            trade_date = row.get('trade_date')
                            if pd.isna(trade_date):
                                continue
                            if not isinstance(trade_date, pd.Timestamp):
                                trade_date = pd.to_datetime(trade_date)

                            record = {'ts_code': code, 'trade_date': trade_date}
                            has_nan = False
                            for df_col, db_col in available.items():
                                v = row.get(df_col)
                                if v is None or pd.isna(v):
                                    has_nan = True
                                    break
                                record[db_col] = float(v)

                            if has_nan:
                                continue
                            all_records.append(record)

                        success_count += 1

                        if idx % 200 == 0:
                            logger.info(f"已计算 {idx}/{len(codes)} 只股票特征")

                    except Exception as e:
                        fail_count += 1
                        if fail_count <= 3:
                            logger.warning(f"计算股票 {code} 特征失败: {e}")
                        continue

                logger.info(f"特征计算完成: 成功={success_count}, 失败={fail_count}, "
                             f"总记录={len(all_records)}")

                # 3. 批量写入（分批避免 max_allowed_packet）
                if all_records:
                    sample = all_records[0]
                    db_cols = list(sample.keys())
                    feature_db_cols = [c for c in db_cols if c not in ('ts_code', 'trade_date')]
                    repository.save_features(session, all_records, feature_db_cols)
                    logger.info(f"批量特征入库完成: {len(all_records)} 条")
                else:
                    logger.warning("无完整特征数据可入库")

                # 返回特征面板（供后续训练使用）
                result_df = repository.load_features_df(session)
                return result_df

    except Exception as e:
        logger.error(f"批量特征计算失败: {e}")
        raise
def _safe_float(val):
    """将值转为 float，NaN 转为 None（MySQL 不接受 NaN）。"""
    try:
        v = float(val)
        return None if math.isnan(v) or math.isinf(v) else v
    except (TypeError, ValueError):
        return None


def incremental_update(stock_codes=None, use_tushare=False, delay=0.5,
                       sectors=None, index_codes=None, start_date=None):
    """
    兼容旧“增量更新”入口，实际只读取 Collector 已落库的数据。

    行情更新请先在 python-collector 执行 ``daily-market`` 或历史补采命令。

    参数:
        stock_codes: 股票代码列表，None则全市场
        use_tushare: 已弃用，仅保留调用兼容
        delay: 已弃用，仅保留调用兼容
        sectors: 行业板块名称列表，如 ["银行", "医药"]
        index_codes: 指数代码列表，如 ["000300"]
    返回:
        DataFrame: 增量更新后的全量面板数据
    """
    del delay, start_date
    if use_tushare:
        logger.info('use_tushare 已弃用；分析应用统一读取 MySQL')
    logger.info('读取 python-collector 已落库的最新市场数据')
    return load_all_stock_data_from_db(
        stock_codes=stock_codes,
        sectors=sectors,
        index_codes=index_codes,
        use_tushare=use_tushare,
    )

    # 以下旧网络增量逻辑保留在本次迁移提交的历史差异中，不再执行。
    import time

    # 获取已有数据的最新日期
    try:
        with DatabaseConnector() as db:
            with db.session_scope() as session:

                # 获取股票列表（先查数据库，如果没有则从数据源拉取）
                basic_df = repository.load_stock_basic_df(session)

                if basic_df.empty:
                    logger.info("数据库中无股票列表，从数据源获取...")
                    collector = DataCollector(use_tushare=use_tushare)
                    try:
                        basic_df = collector.fetch_stock_list()
                        if basic_df.empty:
                            logger.error("获取到的股票列表为空")
                            return pd.DataFrame()

                        # 构建数据库记录（Tushare / Akshare 列名不同）
                        basic_list = []
                        if use_tushare:
                            for _, row in basic_df.iterrows():
                                basic_list.append({
                                    'ts_code': row['ts_code'],
                                    'symbol': row.get('symbol', ''),
                                    'name': row.get('name', ''),
                                    'area': row.get('area', ''),
                                    'industry': row.get('industry', ''),
                                    'list_date': row.get('list_date', None),
                                })
                        else:
                            for _, row in basic_df.iterrows():
                                basic_list.append({
                                    'ts_code': row['代码'],
                                    'symbol': row.get('代码', ''),
                                    'name': row.get('名称', ''),
                                    'area': '',
                                    'industry': row.get('行业', ''),
                                    'list_date': None,
                                })
                    except Exception as e:
                        logger.error(f"获取股票列表失败: {e}", exc_info=True)
                        return pd.DataFrame()

                    if basic_list:
                        # 筛选沪深市场，排除北交所和ST
                        basic_df_tmp = pd.DataFrame(basic_list)
                        basic_df_tmp = filter_stock_list(basic_df_tmp)
                        basic_list = basic_df_tmp.to_dict('records')
                        logger.info(f"筛选后股票列表: {len(basic_list)} 只")

                        repository.save_stock_basic(session, basic_list)
                        logger.info(f"写入 {len(basic_list)} 条股票基本信息到 stock_basic 表")
                    else:
                        logger.error("获取到的股票列表为空")
                        return pd.DataFrame()

                    # 获取列表后也延迟一下
                    time.sleep(delay)

                if stock_codes is None:
                    # 应用板块/指数筛选
                    if sectors or index_codes:
                        from data_processor.stock_filter import resolve_stock_codes
                        filtered_codes = resolve_stock_codes(
                            sectors=sectors, index_codes=index_codes, use_tushare=use_tushare
                        )
                        if filtered_codes is not None:
                            stock_codes = filtered_codes
                            logger.info(f"板块/指数筛选后: {len(stock_codes)} 只股票")
                        else:
                            stock_codes = basic_df['ts_code'].tolist()
                    else:
                        stock_codes = basic_df['ts_code'].tolist()

                logger.info(f"准备采集 {len(stock_codes)} 只股票的增量数据")

                # 查询已有数据的最新日期
                existing_max = repository.get_latest_trade_dates(session)

                collector = DataCollector(use_tushare=use_tushare)
                end_date = datetime.now().strftime('%Y%m%d')
                new_records = []
                fetched_dfs = []  # 收集带换手率等额外字段的内存数据
                success_count = 0
                fail_count = 0

                logger.info(f"数据源: {'Tushare' if use_tushare else 'Akshare'}, 截止日期: {end_date}")

                skip_count = 0
                empty_count = 0
                for idx, code in enumerate(stock_codes, 1):
                    if idx <= 3 or idx % 100 == 0:
                        logger.info(f"进度: {idx}/{len(stock_codes)} | 成功={success_count}, 跳过={skip_count}, 空数据={empty_count}, 失败={fail_count}")
                    last_date = existing_max.get(code)
                    if last_date:
                        # 从已有数据的次日开始采集
                        start = (last_date + timedelta(days=1)).strftime('%Y%m%d')
                        if start > end_date:
                            skip_count += 1
                            if idx <= 3 or idx % 1000 == 0:
                                logger.info(f"[{idx}/{len(stock_codes)}] {code} 数据已是最新，跳过")
                            continue
                    elif start_date:
                        # 无历史数据但有指定起始日期
                        start = pd.to_datetime(start_date).strftime('%Y%m%d')
                    else:
                        # 无历史数据，从2022年开始
                        start = '20220101'

                    # 调用 collector 采集（含重试、列名标准化）
                    df = collector.fetch_single(code, start, end_date)

                    # 请求后延时
                    time.sleep(delay)

                    if df is None:
                        # 采集失败
                        fail_count += 1
                        if fail_count <= 5:
                            logger.warning(f"采集股票 {code} 增量数据失败")
                        elif fail_count == 6:
                            logger.warning("... 后续失败详情不再单独打印")
                        continue

                    if df.empty:
                        empty_count += 1
                        continue

                    success_count += 1

                    # 标准化格式
                    if 'trade_date' in df.columns:
                        df['trade_date'] = pd.to_datetime(df['trade_date'])

                    # 保存带换手率等额外字段的内存数据（供后续构建panel使用）
                    fetched_dfs.append(df.copy())

                    # 过滤掉核心字段含 NaN 的行（保证数据完整性优先）
                    required_cols = ['open', 'high', 'low', 'close', 'vol']
                    df = df.dropna(subset=required_cols)

                    for _, row in df.iterrows():
                        pre_close = _safe_float(row.get('pre_close'))
                        change = _safe_float(row.get('change'))
                        pct_chg = _safe_float(row.get('pct_chg'))
                        amount = _safe_float(row.get('amount'))

                        # 任何可选字段为 NaN 也跳过该行
                        if any(v is None for v in [pre_close, change, pct_chg, amount]):
                            continue

                        new_records.append({
                            'ts_code': code,
                            'trade_date': row['trade_date'],
                            'open': _safe_float(row.get('open', 0)),
                            'high': _safe_float(row.get('high', 0)),
                            'low': _safe_float(row.get('low', 0)),
                            'close': _safe_float(row.get('close', 0)),
                            'pre_close': pre_close,
                            'change': change,
                            'pct_chg': pct_chg,
                            'vol': _safe_float(row.get('vol', 0)),
                            'amount': amount,
                        })

                    if idx <= 3 or idx % 100 == 0:
                        logger.info(f"已处理 {idx}/{len(stock_codes)} 只股票, 成功={success_count}, 失败={fail_count}, 累计记录={len(new_records)}")

                logger.info(f"采集完成: 处理={len(stock_codes)}, 成功={success_count}, 跳过={skip_count}, 空数据={empty_count}, 失败={fail_count}, 新记录={len(new_records)}")

                # 批量写入（使用 INSERT IGNORE 避免重复，分批写入避免超过 max_allowed_packet）
                if new_records:
                    repository.save_daily_records(session, new_records)
                    logger.info(f"增量采集完成，新增 {len(new_records)} 条记录")
                else:
                    logger.info("无新数据需要采集")

                # 构建返回面板：优先使用内存中 fetched 数据（含换手率等额外字段），
                # 历史部分从 parquet 缓存加载（含换手率），而非从 DB（无换手率）
                if fetched_dfs:
                    raw_panel = pd.concat(fetched_dfs, ignore_index=True)
                    raw_panel = raw_panel.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')

                    # 尝试从 parquet 缓存加载历史数据（含换手率）
                    cache_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'models', 'traditional_features', 'raw_panel.parquet')
                    if os.path.exists(cache_path):
                        cached_raw = pd.read_parquet(cache_path, engine='pyarrow')
                        cached_raw['trade_date'] = pd.to_datetime(cached_raw['trade_date'])
                        if stock_codes:
                            cached_raw = cached_raw[cached_raw['ts_code'].isin(stock_codes)]
                        # 只保留不在 raw_panel 中的历史行（这些历史行有 turnover_rate）
                        raw_dates = set(zip(raw_panel['ts_code'], raw_panel['trade_date']))
                        mask = ~cached_raw.apply(lambda r: (r['ts_code'], r['trade_date']) in raw_dates, axis=1)
                        old_data = cached_raw[mask]
                        combined = pd.concat([old_data, raw_panel], ignore_index=True)
                        combined = combined.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
                        combined = combined.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
                        logger.info(f"构建面板: 历史{len(old_data)} + 新增{len(raw_panel)} = 共{len(combined)} 行")
                        return combined

                    # 无 parquet 缓存时从 DB 加载（无换手率，仅兜底）
                    db_panel = load_all_stock_data_from_db(
                        stock_codes=stock_codes, use_tushare=use_tushare
                    )
                    if not db_panel.empty:
                        db_panel['trade_date'] = pd.to_datetime(db_panel['trade_date'])
                        raw_dates = set(zip(raw_panel['ts_code'], raw_panel['trade_date']))
                        mask = ~db_panel.apply(lambda r: (r['ts_code'], r['trade_date']) in raw_dates, axis=1)
                        old_data = db_panel[mask]
                        combined = pd.concat([old_data, raw_panel], ignore_index=True)
                        combined = combined.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
                        combined = combined.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
                        logger.info(f"构建面板(DB兜底): 历史{len(old_data)} + 新增{len(raw_panel)} = 共{len(combined)} 行")
                        return combined
                    else:
                        raw_panel = raw_panel.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
                        logger.info(f"构建面板: 全部新增 {len(raw_panel)} 行")
                        return raw_panel
                else:
                    # 无新数据，从数据库加载
                    logger.info("无新数据，从数据库加载已有数据")
                    return load_all_stock_data_from_db(
                        stock_codes=stock_codes, use_tushare=use_tushare
                    )

    except Exception as e:
        logger.error(f"增量更新失败: {e}")
        return pd.DataFrame()


def build_panel(data_dict):
    """
    将 {股票代码: DataFrame} 字典合并为面板数据。

    参数:
        data_dict: {股票代码: DataFrame}，每个DataFrame包含日线数据
    返回:
        DataFrame: 面板数据（每行=一只股票一天）
    """
    panels = []
    for code, df in data_dict.items():
        df_copy = df.copy()
        df_copy['ts_code'] = code
        panels.append(df_copy)

    if not panels:
        return pd.DataFrame()

    panel = pd.concat(panels, ignore_index=True)

    # 统一日期列名
    if 'trade_date' not in panel.columns and '日期' in panel.columns:
        panel.rename(columns={'日期': 'trade_date'}, inplace=True)

    panel['trade_date'] = pd.to_datetime(panel['trade_date'])
    panel = panel.sort_values(['trade_date', 'ts_code']).reset_index(drop=True)

    logger.info(f"面板数据构建完成: {len(panel)} 行, {panel['ts_code'].nunique()} 只股票")
    return panel


def add_label(panel_df, forward_days=5):
    """
    为面板数据添加 label 列（未来N日收益率）。

    参数:
        panel_df: 面板数据
        forward_days: 远期天数
    返回:
        DataFrame: 添加了 label 列的面板数据
    """
    if type(forward_days) is not int or forward_days < 1:
        raise ValueError('forward_days must be a positive integer')
    panel_df = panel_df.copy()
    panel_df['trade_date'] = pd.to_datetime(panel_df['trade_date'])

    # 按股票分组计算未来收益率
    panel_df = panel_df.sort_values(['ts_code', 'trade_date'])
    panel_df['label_target_date'] = panel_df.groupby('ts_code')['trade_date'].shift(-forward_days)
    panel_df['label'] = panel_df.groupby('ts_code')['close'].transform(
        lambda x: x.shift(-forward_days) / x - 1
    )

    # 同时保留1日远期收益率
    panel_df['future_return_1d'] = panel_df.groupby('ts_code')['close'].transform(
        lambda x: x.shift(-1) / x - 1
    )
    panel_df['future_return_5d'] = panel_df.groupby('ts_code')['close'].transform(
        lambda x: x.shift(-5) / x - 1
    )
    for horizon in (1, 5):
        returns = panel_df[f'future_return_{horizon}d']
        panel_df[f'future_direction_{horizon}d'] = (returns > 0).astype(float).where(returns.notna())
    valid_count = panel_df['label'].notna().sum()
    logger.info(f"添加 label 列（未来{forward_days}日收益率）完成，有效样本: {valid_count}")
    return panel_df


def compute_cross_sectional_features(
    panel_df: pd.DataFrame,
    code_column: str = 'ts_code',
    date_column: str = 'trade_date',
    industry_column: str = 'industry',
) -> pd.DataFrame:
    """
    全市场横截面特征计算（同一交易日内跨股票对比）。

    与单股票时序特征的区别：
        时序特征  ── 沿时间轴为单只股票计算（MA / RSI / MACD 等）
        横截面特征 ── 在同一日期对所有股票横向对比计算

    新增特征:
        market_return          全市场等权平均日收益率
        market_volatility_20d  全市场等权 20 日年化波动率
        industry_return        所属行业等权平均日收益率
        return_vs_market       个股日收益率 − 市场收益率
        return_vs_industry     个股日收益率 − 行业收益率
        volatility_vs_market   个股 20d 波动率 / 市场 20d 波动率
        return_zscore_industry 个股日收益率在行业内的 Z-Score
        volatility_zscore_industry  个股 20d 波动率在行业内的 Z-Score

    Args:
        panel_df:        全市场面板（已含时序特征，需含 return_1d、volatility_20d）
        code_column:     股票代码列名
        date_column:     日期列名
        industry_column: 行业列名（若不存在则跳过行业相关特征）

    Returns:
        pd.DataFrame: 在原始面板上新增横截面特征列（不修改传入对象）
    """
    if panel_df.empty:
        return panel_df

    df = panel_df.copy()
    new_cols_before = set(df.columns)

    # 校验依赖列
    if FEAT_RETURN_1D not in df.columns:
        logger.warning("缺少 %s 列，无法计算横截面特征", FEAT_RETURN_1D)
        return panel_df

    # ── 市场收益率（等权平均）────────────────────────────────────────────────
    market_ret = df.groupby(date_column)[FEAT_RETURN_1D].transform('mean')
    df['market_return'] = market_ret

    # ── 市场波动率（20 日滚动）────────────────────────────────────────────────
    if 'volatility_20d' in df.columns:
        market_vol = (
            df.groupby(date_column)['volatility_20d']
            .transform(lambda x: x.fillna(x.mean()).mean())
        )
        df['market_volatility_20d'] = market_vol

    # ── 个股偏离市场 ─────────────────────────────────────────────────────────
    df['return_vs_market'] = df[FEAT_RETURN_1D] - df['market_return']

    # ── 行业收益率 ────────────────────────────────────────────────────────────
    has_industry = industry_column in df.columns and df[industry_column].notna().any()
    if has_industry:
        industry_ret = (
            df.groupby([date_column, industry_column])[FEAT_RETURN_1D]
            .transform('mean')
        )
        df['industry_return'] = industry_ret
        df['return_vs_industry'] = df[FEAT_RETURN_1D] - df['industry_return']
    else:
        logger.info("面板中无行业列，跳过行业相关横截面特征")

    # ── 波动率对比市场 ────────────────────────────────────────────────────────
    if 'market_volatility_20d' in df.columns and 'volatility_20d' in df.columns:
        df['volatility_vs_market'] = (
            df['volatility_20d'] /
            df['market_volatility_20d'].replace(0, np.nan)
        )

    # ── 行业 Z-Score ──────────────────────────────────────────────────────────
    if has_industry:
        df['return_zscore_industry'] = (
            df.groupby([date_column, industry_column])[FEAT_RETURN_1D]
            .transform(lambda x: (x - x.mean()) / x.std().replace(0, np.nan))
        )
        if 'volatility_20d' in df.columns:
            df['volatility_zscore_industry'] = (
                df.groupby([date_column, industry_column])['volatility_20d']
                .transform(lambda x: (x - x.mean()) / x.std().replace(0, np.nan))
            )

    # 清理 inf
    new_cols = set(df.columns) - new_cols_before
    df[list(new_cols)] = df[list(new_cols)].replace([np.inf, -np.inf], np.nan)

    logger.info("横截面特征计算完成，新增 %d 列: %s",
                len(new_cols), sorted(new_cols))
    return df


def prepare_panel_for_training(panel_df, forward_days=5, use_tushare=False,
                               n_workers=None, use_parallel=True, save_to_db=False, *,
                               keep_unlabelled=False, cache_dir=None):
    """
    完整的训练数据准备流程：
    1. 增量采集最新数据
    2. 逐股计算技术指标（自动检测已有 parquet 缓存，支持增量计算）
    3. 添加 label 列
    4. 清理无效数据

    参数:
        panel_df: 基础面板数据（日线）
        forward_days: 远期收益率天数
        use_tushare: 是否使用Tushare
        n_workers: 并行进程数，None=CPU核心数-1
        use_parallel: 是否使用并行计算
        save_to_db: 是否将特征批量保存到数据库
    返回:
        DataFrame: 可用于训练的面板数据（含特征+label）
    """
    if panel_df.empty:
        logger.warning("面板数据为空")
        return pd.DataFrame()

    # parquet 缓存路径
    stock_universe = sorted(panel_df['ts_code'].dropna().astype(str).unique())
    universe_hash = hashlib.md5('|'.join(stock_universe).encode('utf-8')).hexdigest()[:10]
    universe_key = f"{len(stock_universe)}_{universe_hash}"
    CACHE_DIR = os.fspath(cache_dir) if cache_dir is not None else os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'models', 'traditional_features', universe_key, 'validation_v1'
    )
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, 'panel_features.parquet')
    raw_cache_path = os.path.join(CACHE_DIR, 'raw_panel.parquet')

    # 自动检测：已有缓存时走增量
    # Caches without a verified full source identity are never incrementally
    # mixed. Rebuild from supplied history in the isolated validation namespace.
    if not panel_df.empty:
        # 全量计算（原有逻辑）
        engineer = FeatureEngineer()
        if use_parallel:
            logger.info("使用多进程并行计算技术指标...")
            featured_panel = engineer.calculate_features_parallel(
                panel_df, n_workers=n_workers, forward_days=forward_days)
        else:
            logger.info("开始逐股计算技术指标...")
            results = []
            codes = panel_df['ts_code'].unique()

            for idx, code in enumerate(codes, 1):
                stock_df = panel_df[panel_df['ts_code'] == code].copy()
                stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)

                if len(stock_df) < 60:
                    continue

                try:
                    stock_featured = engineer.calculate_all_features(stock_df)
                    results.append(stock_featured)

                    if idx % 200 == 0:
                        logger.info(f"已计算 {idx}/{len(codes)} 只股票的特征")
                except Exception as e:
                    logger.warning(f"计算股票 {code} 特征失败: {e}")
                    continue

            if not results:
                logger.warning("无股票成功计算特征")
                return pd.DataFrame()

            featured_panel = pd.concat(results, ignore_index=True)

        # 保存原始面板缓存（供下次增量使用，保存完整数据含换手率等字段）
        save_raw_panel(panel_df, raw_cache_path)
        logger.info(f"原始面板缓存已保存: {raw_cache_path}")

    # 添加横截面特征（在 label 之前，因为横截面特征需要 return_1d 等时序特征）
    featured_panel = compute_cross_sectional_features(featured_panel)

    # 添加 label
    featured_panel = add_label(featured_panel, forward_days=forward_days)

    # 清理无效数据（只保留 label 和核心标识列有效的行）
    featured_panel = featured_panel.dropna(subset=['ts_code'])
    featured_panel = featured_panel.replace([np.inf, -np.inf], np.nan)
    featured_panel = featured_panel.dropna(subset=['ts_code'])
    featured_panel.to_parquet(cache_path, engine='pyarrow', index=False)
    if not keep_unlabelled:
        featured_panel = featured_panel.dropna(subset=['label'])

    logger.info(f"训练数据准备完成: {len(featured_panel)} 行, "
                f"{featured_panel['ts_code'].nunique()} 只股票, "
                f"{featured_panel['trade_date'].nunique()} 个交易日")

    # 可选：批量保存特征到数据库
    if save_to_db:
        save_features_batch_to_db(featured_panel)

    return featured_panel


def _prepare_panel_incremental(panel_df, cache_path, raw_cache_path, forward_days, n_workers, use_parallel):
    """
    增量模式：加载已有特征缓存，只计算新增日期的特征。
    直接使用传入的 panel_df（含完整历史+新数据+换手率），不再依赖旧 parquet 缓存的原始面板。
    """

    # 已有缓存：直接加载已有特征面板
    cached = pd.read_parquet(cache_path, engine='pyarrow')
    existing_last = pd.to_datetime(cached['trade_date']).max()
    logger.info(f"已有特征缓存: {len(cached)} 行, 最新日期 {existing_last}")

    # 新数据
    new_panel = panel_df.copy()
    new_panel['trade_date'] = pd.to_datetime(new_panel['trade_date'])

    if new_panel['trade_date'].max() <= existing_last:
        logger.info("无新数据，直接加载已有特征缓存")
        return cached

    # 使用完整的 panel_df 作为原始数据（含换手率等字段），
    # 不再拼接旧 parquet 缓存（旧缓存可能缺少 turnover_rate 等列）
    combined_raw = new_panel.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')

    # 更新缓存（保存完整数据含换手率等字段）
    save_raw_panel(combined_raw, raw_cache_path)
    logger.info(f"原始面板缓存已更新: {len(combined_raw)} 行")

    # 增量计算特征：逐股计算，只对新增数据
    engineer = FeatureEngineer()
    all_results = []
    codes = combined_raw['ts_code'].unique()

    for idx, code in enumerate(codes, 1):
        stock_df = combined_raw[combined_raw['ts_code'] == code].copy()
        stock_df = stock_df.sort_values('trade_date').reset_index(drop=True)

        if len(stock_df) < 60:
            continue

        try:
            stock_featured = engineer.calculate_all_features(stock_df)

            # 如果股票在已有缓存中，只保留新增日期
            if code in cached['ts_code'].values:
                stock_featured = stock_featured[stock_featured['trade_date'] > existing_last]

            if not stock_featured.empty:
                all_results.append(stock_featured)

            if idx % 200 == 0:
                logger.info(f"已计算 {idx}/{len(codes)} 只股票特征")
        except Exception as e:
            logger.warning(f"计算股票 {code} 特征失败: {e}")
            continue

    if not all_results:
        logger.warning("无新特征数据")
        return cached

    new_panel_featured = pd.concat(all_results, ignore_index=True)
    featured_panel = pd.concat([cached, new_panel_featured], ignore_index=True)
    logger.info(f"增量特征完成: {len(new_panel_featured)} 行新增, {len(featured_panel)} 行总计")

    # 保存特征缓存
    featured_panel.to_parquet(cache_path, engine='pyarrow', index=False)
    logger.info(f"特征缓存已更新: {cache_path}")

    return featured_panel


# 数据库特征列映射：DataFrame 列名（v2.0 snake_case）-> 数据库列名
# feature_engineer v2.0 输出列名已与 DB 对齐，映射为 1:1（保留以供外部使用）
FEATURE_COL_MAP = {
    # 均线
    'ma5': 'ma5', 'ma10': 'ma10', 'ma20': 'ma20', 'ma60': 'ma60',
    # EMA
    'ema5': 'ema5', 'ema10': 'ema10', 'ema20': 'ema20',
    'ema26': 'ema26', 'ema60': 'ema60', 'ema120': 'ema120',
    # MACD
    'macd_dif': 'macd_dif', 'macd_dea': 'macd_dea', 'macd_hist': 'macd_hist',
    # RSI
    'rsi': 'rsi',
    # 布林带
    'bb_upper': 'bb_upper', 'bb_middle': 'bb_middle',
    'bb_lower': 'bb_lower', 'bb_width': 'bb_width',
    # KDJ
    'kdj_k': 'kdj_k', 'kdj_d': 'kdj_d', 'kdj_j': 'kdj_j',
    # 成交量
    'vol_ma5': 'vol_ma5', 'vol_ma10': 'vol_ma10',
    'volume_ratio': 'volume_ratio', 'mfi14': 'mfi14',
    # 收益率 & 标签
    'return_1d': 'return_1d', 'return_5d': 'return_5d',
    'return_10d': 'return_10d', 'log_return': 'log_return',
    # [防泄漏] future_return / future_direction 不入库
    # 它们只作为训练标签，由 pipeline.py 在划分训练集时临时计算，
    # 绝不能存入 stock_features 表，否则线上预测时可能被误用为特征。
    # 换手率
    'turnover_rate_5': 'turnover_rate_5',
    'turnover_rate_60': 'turnover_rate_60',
    'turnover_rate_120': 'turnover_rate_120',
    # 情绪
    'br': 'br', 'ar': 'ar',
    # 风险（v2.0 更名：原 Variance* → volatility_*）
    'volatility_20d': 'volatility_20d', 'volatility_60d': 'volatility_60d',
    'volatility_120d': 'volatility_120d',
    'skewness_20d': 'skewness_20d', 'skewness_60d': 'skewness_60d',
    'skewness_120d': 'skewness_120d',
    'kurtosis_20d': 'kurtosis_20d', 'kurtosis_60d': 'kurtosis_60d',
    'kurtosis_120d': 'kurtosis_120d',
    # 动量
    'arron_up_25': 'arron_up_25', 'arron_down_25': 'arron_down_25',
    'bear_power': 'bear_power', 'bull_power': 'bull_power',
    'bias5': 'bias5', 'bias10': 'bias10', 'bias20': 'bias20', 'bias60': 'bias60',
    'cci10': 'cci10', 'cci15': 'cci15', 'cci20': 'cci20', 'cci88': 'cci88',
    'cr20': 'cr20', 'mass': 'mass',
    # 技术信号
    'golden_cross': 'golden_cross', 'death_cross': 'death_cross',
    'macd_golden_cross': 'macd_golden_cross',
    'rsi_oversold': 'rsi_oversold', 'rsi_overbought': 'rsi_overbought',
}


def save_features_batch_to_db(featured_panel):
    """
    将批量计算的特征面板数据写入 stock_features 表。

    参数:
        featured_panel: 包含技术指标的 DataFrame（需含 ts_code, trade_date 列）
    """
    if featured_panel.empty:
        logger.warning("特征面板为空，跳过入库")
        return

    logger.info(f"开始批量保存特征到 stock_features 表，共 {len(featured_panel)} 行...")

    try:
        with DatabaseConnector() as db:
            with db.session_scope() as session:
                # 筛选 DataFrame 中实际存在的特征列
                available_map = {df_col: db_col for df_col, db_col in FEATURE_COL_MAP.items()
                                 if df_col in featured_panel.columns}
                available_df_cols = list(available_map.keys())
                missing_cols = [df_col for df_col in FEATURE_COL_MAP if df_col not in featured_panel.columns]
                if missing_cols:
                    logger.warning(f"DataFrame 中缺少以下特征列: {missing_cols}")

                logger.info(f"可用特征列: {len(available_df_cols)}/{len(FEATURE_COL_MAP)}")

                # 向量化操作：提取需要的列 + 过滤 NaN（比 iterrows 快 100 倍）
                cols_needed = ['ts_code', 'trade_date'] + available_df_cols
                sub = featured_panel[cols_needed].copy()

                total_before = len(sub)
                # 删除 ts_code 或 trade_date 为空的行
                sub = sub.dropna(subset=['ts_code', 'trade_date'])
                # 删除任一特征列为 NaN 的行
                sub = sub.dropna(subset=available_df_cols)
                skipped = total_before - len(sub)
                logger.info(f"NaN 过滤完成: {len(sub)} 行有效, {skipped} 行被过滤")

                # 重命名列
                sub.rename(columns=available_map, inplace=True)

                # 确保 trade_date 是日期类型
                sub['trade_date'] = pd.to_datetime(sub['trade_date'])

                # 转为字典列表（向量化转换，比逐行构建快很多）
                db_cols = list(available_map.values())
                records = sub[['ts_code', 'trade_date'] + db_cols].to_dict('records')

                if records:
                    feature_db_cols = [c for c in db_cols if c not in ('ts_code', 'trade_date')]
                    repository.save_features(session, records, feature_db_cols)
                    logger.info(f"特征批量入库完成: 写入 {len(records)} 条, 过滤 {skipped} 条不完整行")
                else:
                    logger.warning(f"无完整特征数据可入库，过滤了 {skipped} 行")

    except Exception as e:
        logger.error(f"特征批量入库失败: {e}")
        raise
