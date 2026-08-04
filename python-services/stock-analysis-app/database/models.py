"""
数据库表结构定义
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, Date, Index, Text, UniqueConstraint
from sqlalchemy.sql import func
from database.db_connector import Base


# ── 表名常量（供外部模块引用，避免硬编码）───────────────────

TABLE_STOCK_BASIC = 'stock_basic'
TABLE_STOCK_DAILY = 'stock_daily'
TABLE_STOCK_FEATURES = 'stock_features'
TABLE_ANALYSIS_RESULT = 'analysis_result'
TABLE_COLLECTION_TASK = 'collection_task'


class StockBasic(Base):
    """股票基本信息表"""
    __tablename__ = 'stock_basic'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(10), unique=True, nullable=False, comment='TS代码')
    symbol = Column(String(10), comment='股票代码')
    name = Column(String(50), comment='股票名称')
    area = Column(String(20), comment='所在地域')
    industry = Column(String(30), comment='所属行业')
    list_date = Column(Date, comment='上市日期')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        Index('idx_symbol', 'symbol'),
        Index('idx_industry', 'industry'),
    )


class StockDaily(Base):
    """股票日线数据表"""
    __tablename__ = 'stock_daily'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(10), nullable=False, comment='TS代码')
    trade_date = Column(Date, nullable=False, comment='交易日期')
    open = Column(Float, comment='开盘价')
    high = Column(Float, comment='最高价')
    low = Column(Float, comment='最低价')
    close = Column(Float, comment='收盘价')
    pre_close = Column(Float, comment='昨收价')
    change = Column(Float, comment='涨跌额')
    pct_chg = Column(Float, comment='涨跌幅')
    vol = Column(Float, comment='成交量（手）')
    amount = Column(Float, comment='成交额（千元）')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')

    __table_args__ = (
        Index('idx_code_date', 'ts_code', 'trade_date', unique=True),
        Index('idx_trade_date', 'trade_date'),
    )


class StockFeatures(Base):
    """股票技术指标表（全量特征，供传统管线 XGBoost/LSTM 复用）"""
    __tablename__ = 'stock_features'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(10), nullable=False, comment='TS代码')
    trade_date = Column(Date, nullable=False, comment='交易日期')

    # 均线
    ma5 = Column(Float, comment='5日均线')
    ma10 = Column(Float, comment='10日均线')
    ma20 = Column(Float, comment='20日均线')
    ma60 = Column(Float, comment='60日均线')

    # EMA
    ema5 = Column(Float, comment='5日指数移动平均')
    ema10 = Column(Float, comment='10日指数移动平均')
    ema20 = Column(Float, comment='20日指数移动平均')
    ema26 = Column(Float, comment='26日指数移动平均')
    ema60 = Column(Float, comment='60日指数移动平均')
    ema120 = Column(Float, comment='120日指数移动平均')

    # MACD
    macd = Column(Float, comment='MACD_DIF')
    macd_signal = Column(Float, comment='MACD_DEA')
    macd_hist = Column(Float, comment='MACD柱状图')

    # RSI
    rsi = Column(Float, comment='RSI指标')

    # 布林带
    bb_upper = Column(Float, comment='布林带上轨')
    bb_middle = Column(Float, comment='布林带中轨')
    bb_lower = Column(Float, comment='布林带下轨')
    bb_width = Column(Float, comment='布林带宽度')

    # KDJ
    k = Column(Float, comment='K值')
    d = Column(Float, comment='D值')
    j = Column(Float, comment='J值')

    # 成交量指标
    vol_ma5 = Column(Float, comment='5日成交量均线')
    vol_ma10 = Column(Float, comment='10日成交量均线')
    volume_ratio = Column(Float, comment='量比')
    mfi14 = Column(Float, comment='资金流量指标')

    # 收益率 & 标签
    return_1d = Column(Float, comment='1日收益率')
    return_5d = Column(Float, comment='5日收益率')
    return_10d = Column(Float, comment='10日收益率')
    log_return = Column(Float, comment='对数收益率')
    future_return_1d = Column(Float, comment='未来1日收益率')
    future_return_5d = Column(Float, comment='未来5日收益率')
    future_direction_1d = Column(Float, comment='未来1日涨跌方向')
    future_direction_5d = Column(Float, comment='未来5日涨跌方向')

    # 换手率
    turnover_rate_5 = Column(Float, comment='5日换手率')
    turnover_rate_60 = Column(Float, comment='60日换手率')
    turnover_rate_120 = Column(Float, comment='120日换手率')

    # 情绪指标
    br = Column(Float, comment='BR指标')
    ar = Column(Float, comment='AR指标')

    # 风险指标
    variance20 = Column(Float, comment='20日年化收益方差')
    variance60 = Column(Float, comment='60日年化收益方差')
    variance120 = Column(Float, comment='120日年化收益方差')
    skewness20 = Column(Float, comment='20日偏度')
    skewness60 = Column(Float, comment='60日偏度')
    skewness120 = Column(Float, comment='120日偏度')
    kurtosis20 = Column(Float, comment='20日峰度')
    kurtosis60 = Column(Float, comment='60日峰度')
    kurtosis120 = Column(Float, comment='120日峰度')

    # 动量指标
    arron_up_25 = Column(Float, comment='Aroon上轨')
    arron_down_25 = Column(Float, comment='Aroon下轨')
    bear_power = Column(Float, comment='空头力道')
    bull_power = Column(Float, comment='多头力道')
    bias5 = Column(Float, comment='5日乖离率')
    bias10 = Column(Float, comment='10日乖离率')
    bias20 = Column(Float, comment='20日乖离率')
    bias60 = Column(Float, comment='60日乖离率')
    cci10 = Column(Float, comment='10日顺势指标')
    cci15 = Column(Float, comment='15日顺势指标')
    cci20 = Column(Float, comment='20日顺势指标')
    cci88 = Column(Float, comment='88日顺势指标')
    cr20 = Column(Float, comment='CR指标')
    mass = Column(Float, comment='梅斯线')

    # 技术信号
    golden_cross = Column(Float, comment='金叉信号')
    death_cross = Column(Float, comment='死叉信号')
    macd_golden_cross = Column(Float, comment='MACD金叉信号')
    rsi_oversold = Column(Float, comment='RSI超卖信号')
    rsi_overbought = Column(Float, comment='RSI超买信号')

    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')

    __table_args__ = (
        Index('idx_feature_code_date', 'ts_code', 'trade_date', unique=True),
    )


class AnalysisResult(Base):
    """分析结果表"""
    __tablename__ = 'analysis_result'

    id = Column(Integer, primary_key=True, autoincrement=True)
    ts_code = Column(String(10), nullable=False, comment='TS代码')
    analysis_date = Column(Date, nullable=False, comment='分析日期')
    analysis_type = Column(String(50), comment='分析类型')
    result = Column(Text, comment='分析结果（JSON格式）')
    prediction = Column(Float, comment='预测值')
    confidence = Column(Float, comment='置信度')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')

    __table_args__ = (
        Index('idx_analysis_code', 'ts_code'),
        Index('idx_analysis_date', 'analysis_date'),
    )


class CollectionTask(Base):
    """采集任务状态表（V0.2 新增）"""
    __tablename__ = TABLE_COLLECTION_TASK

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_type = Column(String(20), nullable=False, comment='任务类型: daily/history/basic/retry')
    business_date = Column(String(10), nullable=False, comment='业务日期 YYYYMMDD')
    source = Column(String(20), nullable=False, comment='数据源: tushare/akshare')
    status = Column(String(20), nullable=False, default='PENDING',
                    comment='状态: PENDING/RUNNING/SUCCESS/FAILED')
    record_count = Column(Integer, default=0, comment='采集记录数')
    retry_count = Column(Integer, default=0, comment='重试次数')
    error_message = Column(Text, comment='错误信息')
    started_at = Column(DateTime, comment='开始时间')
    finished_at = Column(DateTime, comment='完成时间')
    created_at = Column(DateTime, server_default=func.now(), comment='创建时间')
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), comment='更新时间')

    __table_args__ = (
        UniqueConstraint('task_type', 'business_date', 'source', name='uq_task_date_source'),
        Index('idx_task_status', 'status'),
        Index('idx_task_type_date', 'task_type', 'business_date'),
    )