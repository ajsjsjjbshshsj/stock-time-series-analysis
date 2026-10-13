"""Insert-only stock identity SQL; callers retain fencing and IPO conflict policy."""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

FIELDS = ('ts_code', 'symbol', 'name', 'area', 'industry', 'list_date')


class ReferenceInsertConflict(ValueError):
    """A duplicate insert did not leave a usable identity row."""


def insert_missing_reference(session, row):
    """Return existing list_date row (including insert race), or None after insertion.

    Never commit here or overwrite existing data. The caller owns the outer
    transaction; a savepoint confines duplicate-key recovery to this insertion.
    """
    dialect = session.get_bind().dialect.name
    suffix = ' FOR UPDATE' if dialect == 'mysql' else ''
    query = text('SELECT list_date FROM stock_basic WHERE ts_code=:code' + suffix)
    previous = session.execute(query, dict(code=row['ts_code'])).first()
    if previous is not None:
        return previous
    # Python sqlite3's legacy mode does not BEGIN on SELECT. Without this,
    # releasing the first savepoint commits the insert outside the caller's
    # rollback boundary. MySQL already has a real transactional outer scope.
    if dialect == 'sqlite':
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql('BEGIN')
    try:
        with session.begin_nested():
            session.execute(text('INSERT INTO stock_basic (ts_code,symbol,name,area,industry,list_date) '
                'VALUES (:ts_code,:symbol,:name,:area,:industry,:list_date)'),
                {field: row[field] for field in FIELDS})
    except IntegrityError as error:
        duplicate = (dialect == 'mysql' and error.orig.args and error.orig.args[0] == 1062) or (
            dialect == 'sqlite' and getattr(error.orig, 'sqlite_errorcode', None) in (2067, 1555))
        if not duplicate:
            raise
        previous = session.execute(query, dict(code=row['ts_code'])).first()
        if previous is None or previous[0] is None:
            raise ReferenceInsertConflict('REFERENCE_INSERT_CONFLICT') from None
        return previous
    return None
