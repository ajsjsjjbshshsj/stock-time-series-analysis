-- One-time operator DDL for an existing V0.9_005 schema. Stop API/workers first.
-- Prefer `training_worker.py migrate-all-market`, which inspects and skips
-- already-applied changes. Never run this automatically from API or worker.
ALTER TABLE analysis_training_task MODIFY COLUMN binding LONGTEXT NOT NULL;
ALTER TABLE analysis_training_task ADD COLUMN progress TEXT NULL;
ALTER TABLE analysis_training_event ADD COLUMN detail TEXT NULL;
