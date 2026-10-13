"""Task-level pacing waits; network/source retries have their own bounded policy."""
RETRYABLE_TASK_ERRORS = frozenset({'MINUTE_QUOTA', 'HOURLY_QUOTA', 'QUOTA_COOLDOWN', 'REQUEST_STATE_BUSY'})
