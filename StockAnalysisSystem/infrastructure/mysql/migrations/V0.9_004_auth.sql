-- Explicit operator migration only. Does not change market/model tables.
CREATE TABLE IF NOT EXISTS analysis_auth_control (id INT PRIMARY KEY) ENGINE=InnoDB;
INSERT IGNORE INTO analysis_auth_control(id) VALUES (1);
CREATE TABLE IF NOT EXISTS analysis_auth_user (
 id VARCHAR(32) PRIMARY KEY, username VARCHAR(64) NOT NULL UNIQUE,
 password_hash VARCHAR(512) NOT NULL, role VARCHAR(8) NOT NULL,
 enabled BOOLEAN NOT NULL, must_change_password BOOLEAN NOT NULL,
 CONSTRAINT ck_analysis_auth_role CHECK (role IN ('user','admin'))
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS analysis_auth_session (
 token_hash VARCHAR(64) PRIMARY KEY, user_id VARCHAR(32) NULL,
 created_at BIGINT NOT NULL, expires_at BIGINT NOT NULL, last_seen BIGINT NOT NULL,
 INDEX ix_analysis_auth_session_user_id(user_id),
 FOREIGN KEY (user_id) REFERENCES analysis_auth_user(id) ON DELETE CASCADE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS analysis_auth_limit (
 key_hash VARCHAR(64) PRIMARY KEY, window_start BIGINT NOT NULL, attempts INT NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS analysis_auth_audit (
 id VARCHAR(32) PRIMARY KEY, actor_id VARCHAR(32) NULL, target_id VARCHAR(32) NULL,
 action VARCHAR(32) NOT NULL, created_at BIGINT NOT NULL
) ENGINE=InnoDB;
