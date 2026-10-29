-- Explicit additive migration; applying it does not activate a generation.
-- Namespace/partition validation makes output offsets comparable for each key.
CREATE DATABASE IF NOT EXISTS stock_analytics;

CREATE TABLE IF NOT EXISTS stock_analytics.generation_daily_indicators
(
    generation String,
    event_id String,
    source_event_id String,
    trace_id String,
    ts_code String,
    trade_date Date,
    source String,
    source_event_time DateTime64(9, 'UTC'),
    close Decimal(38, 6),
    volume Decimal(38, 6),
    pct_chg Nullable(Decimal(38, 6)),
    ma5 Nullable(Decimal(38, 6)),
    ma10 Nullable(Decimal(38, 6)),
    ma20 Nullable(Decimal(38, 6)),
    vol_ma5 Nullable(Decimal(38, 6)),
    vol_ma10 Nullable(Decimal(38, 6)),
    volume_ratio Nullable(Decimal(38, 6)),
    window_size UInt8,
    is_warmup Bool,
    calculation_time DateTime64(9, 'UTC'),
    schema_version UInt32,
    source_revision UInt64,
    input_topic String,
    input_partition Int32,
    input_offset UInt64,
    kafka_topic String,
    kafka_partition Int32,
    kafka_offset UInt64,
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY (generation, toYYYYMM(trade_date))
ORDER BY (generation, ts_code, trade_date);

-- Per-key seed evidence is NOT global readiness. Repair evidence is never cleared.
CREATE TABLE IF NOT EXISTS stock_analytics.generation_indicator_events
(
    generation String,
    ts_code String,
    trade_date Nullable(Date),
    schema_version UInt32,
    kind String,
    reason Nullable(String),
    source_revision UInt64,
    input_topic String,
    input_partition Int32,
    input_offset UInt64,
    kafka_topic String,
    kafka_partition Int32,
    kafka_offset UInt64,
    version UInt64
)
ENGINE = ReplacingMergeTree(version)
PARTITION BY generation
ORDER BY (generation, ts_code, input_partition, input_offset, kind);
