-- V0.4 原始市场参考数据表：每日估值指标与指数/行业成分股快照。

-- 成分股任务需以 type:code:date 作为幂等业务键；旧 YYYYMMDD 值完全兼容。
ALTER TABLE `collection_task`
    MODIFY COLUMN `business_date` VARCHAR(80) NOT NULL COMMENT '业务日期或业务幂等键';

CREATE TABLE IF NOT EXISTS `stock_daily_basic` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `ts_code` VARCHAR(10) NOT NULL COMMENT '统一股票代码',
    `trade_date` DATE NOT NULL COMMENT '交易日期',
    `turnover_rate` DECIMAL(20, 6) NULL COMMENT '换手率（%）',
    `pe` DECIMAL(20, 6) NULL COMMENT '市盈率',
    `pe_ttm` DECIMAL(20, 6) NULL COMMENT '滚动市盈率',
    `pb` DECIMAL(20, 6) NULL COMMENT '市净率',
    `ps` DECIMAL(20, 6) NULL COMMENT '市销率',
    `total_mv` DECIMAL(24, 4) NULL COMMENT '总市值（万元）',
    `source` VARCHAR(20) NOT NULL COMMENT '数据源',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_daily_basic_code_date` (`ts_code`, `trade_date`),
    KEY `idx_daily_basic_trade_date` (`trade_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='原始每日估值与换手率数据';

CREATE TABLE IF NOT EXISTS `stock_constituent` (
    `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `group_type` VARCHAR(20) NOT NULL COMMENT '分组类型：index/industry',
    `group_code` VARCHAR(30) NOT NULL COMMENT '指数或行业代码',
    `ts_code` VARCHAR(10) NOT NULL COMMENT '统一股票代码',
    `as_of_date` DATE NOT NULL COMMENT '快照日期',
    `weight` DECIMAL(20, 6) NULL COMMENT '成分权重',
    `source` VARCHAR(20) NOT NULL COMMENT '数据源',
    `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_constituent_snapshot` (`group_type`, `group_code`, `ts_code`, `as_of_date`),
    KEY `idx_constituent_group_date` (`group_type`, `group_code`, `as_of_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='指数和行业成分股快照';
