"""Shared SQL transaction and encoding helpers, without application configuration."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json

from sqlalchemy.exc import DBAPIError


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stamp(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('AWARE_TIMESTAMP_REQUIRED')
    # Fixed-width UTC SQL literals compare chronologically and avoid Python 3.12's
    # deprecated sqlite datetime adapter without changing process-global adapters.
    return parsed.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f')


def utc(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed.replace(tzinfo=timezone.utc).isoformat()


@contextmanager
def transaction(factory):
    failure = None
    try:
        with factory.begin() as session:
            yield session
    except DBAPIError as error:
        # Classification only; never preserve SQL parameters/connection errors in output.
        args = getattr(error.orig, 'args', ())
        missing = bool(args and args[0] == 1146) or 'no such table' in str(error.orig).lower()
        failure = 'MIGRATION_REQUIRED' if missing else 'STORAGE_UNAVAILABLE'
    if failure:
        raise RuntimeError(failure)


def current_lock(session):
    return ' FOR UPDATE' if session.get_bind().dialect.name == 'mysql' else ''
