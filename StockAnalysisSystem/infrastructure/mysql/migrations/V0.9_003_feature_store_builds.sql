-- Explicit metadata installation only. The guarded builder creates isolated
-- data tables and publishes them after verification; imports/GET create none.
CREATE TABLE IF NOT EXISTS stock_feature_build (
    build_id CHAR(32) NOT NULL,
    storage_contract VARCHAR(64) NOT NULL,
    algorithm_version VARCHAR(32) NOT NULL,
    columns_sha256 CHAR(64) NOT NULL,
    code_sha256 CHAR(64) NOT NULL,
    source_sha256 CHAR(64) NULL,
    output_sha256 CHAR(64) NULL,
    source_start DATE NOT NULL,
    source_cutoff DATE NOT NULL,
    row_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    stock_count BIGINT UNSIGNED NOT NULL DEFAULT 0,
    state VARCHAR(16) NOT NULL,
    active_slot VARCHAR(32) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    verified_at DATETIME NULL,
    failure_code VARCHAR(64) NULL,
    PRIMARY KEY (build_id),
    UNIQUE KEY uq_feature_active_slot (active_slot),
    CONSTRAINT ck_feature_build_state CHECK (state IN ('BUILDING', 'READY', 'FAILED')),
    CONSTRAINT ck_feature_active_ready CHECK (active_slot IS NULL OR state = 'READY'),
    CONSTRAINT ck_feature_source_range CHECK (source_start <= source_cutoff)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
