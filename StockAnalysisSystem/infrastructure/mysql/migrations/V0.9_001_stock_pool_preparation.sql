-- Additive only. Apply explicitly; the service never initializes its own schema.
CREATE TABLE IF NOT EXISTS stock_pool_version (
    pool_version VARCHAR(96) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    pool_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    metadata_json LONGTEXT NOT NULL,
    member_sha256 CHAR(64) COLLATE utf8mb4_bin NOT NULL,
    stock_count INT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    KEY idx_pool_id (pool_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS stock_pool_member (
    pool_version VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    ts_code VARCHAR(10) COLLATE utf8mb4_bin NOT NULL,
    ordinal INT NOT NULL,
    member_json LONGTEXT NOT NULL,
    PRIMARY KEY (pool_version,ts_code),
    UNIQUE KEY uq_pool_ordinal (pool_version,ordinal),
    CONSTRAINT fk_member_version FOREIGN KEY (pool_version) REFERENCES stock_pool_version(pool_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS stock_pool_active (
    pool_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    pool_version VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    CONSTRAINT fk_active_version FOREIGN KEY (pool_version) REFERENCES stock_pool_version(pool_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS data_preparation_task (
    task_id CHAR(32) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    identity CHAR(64) COLLATE utf8mb4_bin NOT NULL,
    active_identity CHAR(64) COLLATE utf8mb4_bin NULL,
    request_json LONGTEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    phase VARCHAR(24) NOT NULL,
    owner_token CHAR(32) COLLATE utf8mb4_bin NULL,
    owner_json LONGTEXT NULL,
    lease_until DATETIME(6) NULL,
    retry_not_before DATETIME(6) NULL,
    progress_json LONGTEXT NOT NULL,
    result_json LONGTEXT NULL,
    error_kind VARCHAR(32) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_active_preparation (active_identity),
    KEY idx_task_identity (identity),
    KEY idx_preparation_queue (status,retry_not_before,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS market_source_evidence (
    evidence_id CHAR(64) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    kind VARCHAR(24) NOT NULL,
    ts_code VARCHAR(10) COLLATE utf8mb4_bin NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    provider VARCHAR(64) NOT NULL,
    relative_path VARCHAR(512) COLLATE utf8mb4_bin NOT NULL,
    manifest_sha256 CHAR(64) COLLATE utf8mb4_bin NOT NULL,
    content_sha256 CHAR(64) COLLATE utf8mb4_bin NOT NULL,
    UNIQUE KEY uq_source_content (kind,ts_code,start_date,end_date,content_sha256),
    KEY idx_source_coverage (kind,ts_code,start_date,end_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
