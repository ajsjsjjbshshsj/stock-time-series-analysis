-- Explicit migration. No automatic DDL on HTTP requests or worker startup.
CREATE TABLE IF NOT EXISTS market_collection_request (
 id VARCHAR(32) PRIMARY KEY, actor_id VARCHAR(32) NOT NULL,
 request_key VARCHAR(32) NOT NULL, payload_sha256 VARCHAR(64) NOT NULL,
 start_date VARCHAR(10) NOT NULL, end_date VARCHAR(10) NOT NULL,
 target_task VARCHAR(32), target_attempt INTEGER, status VARCHAR(24) NOT NULL,
 batch_id VARCHAR(40), error_kind VARCHAR(48), retry_not_before VARCHAR(32),
 created_at VARCHAR(32) NOT NULL, updated_at VARCHAR(32) NOT NULL,
 CONSTRAINT uq_collection_request_key UNIQUE (actor_id,request_key),
 INDEX ix_collection_request_pending (status,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS market_collection_request_day (
 request_id VARCHAR(32) NOT NULL, task_id VARCHAR(32) NOT NULL,
 PRIMARY KEY(request_id,task_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
