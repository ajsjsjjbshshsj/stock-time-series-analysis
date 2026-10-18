-- Explicit operator migration. Never alters market/auth/model tables.
CREATE TABLE IF NOT EXISTS analysis_training_slot (
 id INT PRIMARY KEY, task_id VARCHAR(32), worker_id VARCHAR(32),
 generation BIGINT NOT NULL, lease_until BIGINT, quarantined BOOLEAN NOT NULL
) ENGINE=InnoDB;
INSERT IGNORE INTO analysis_training_slot(id,generation,quarantined) VALUES (1,0,FALSE);
CREATE TABLE IF NOT EXISTS analysis_training_task (
 seq INT AUTO_INCREMENT PRIMARY KEY, id VARCHAR(32) NOT NULL UNIQUE,
 actor_id VARCHAR(32) NOT NULL, idempotency_key VARCHAR(64) NOT NULL,
 request_hash VARCHAR(64) NOT NULL, request TEXT NOT NULL, binding TEXT NOT NULL,
 state VARCHAR(24) NOT NULL, stage VARCHAR(16) NOT NULL, created_at BIGINT NOT NULL,
 started_at BIGINT, heartbeat_at BIGINT, finished_at BIGINT, worker_id VARCHAR(32),
 generation BIGINT, failure_code VARCHAR(32), candidate TEXT, event_count INT NOT NULL,
 CONSTRAINT uq_training_actor_key UNIQUE(actor_id,idempotency_key),
 INDEX ix_analysis_training_task_state(state)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS analysis_training_event (
 id INT AUTO_INCREMENT PRIMARY KEY, task_id VARCHAR(32) NOT NULL,
 event VARCHAR(24) NOT NULL, created_at BIGINT NOT NULL,
 FOREIGN KEY (task_id) REFERENCES analysis_training_task(id),
 INDEX ix_analysis_training_event_task_id(task_id)
) ENGINE=InnoDB;
