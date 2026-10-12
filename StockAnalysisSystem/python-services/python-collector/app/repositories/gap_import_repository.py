"""Insert-only frozen targets; no full-table price baseline or verification."""
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from app.market_data.repair_contract import DAILY, BASIC
from app.market_data.repair_snapshot import equal_business, sql_value
from app.preparation.contracts import iso
from app.nightly.contracts import is_a_share
from app.repositories.preparation_import_repository import PreparationImportRepository


def validate_targets(table, rows, allowed_keys):
    fields = set(DAILY if table == 'stock_daily' else BASIC+['source'] if table == 'stock_daily_basic' else [])
    allowed = {tuple(key) for key in allowed_keys}
    keys = [(row['ts_code'], row['trade_date']) for row in rows]
    if (not fields or len(keys) != len(set(keys)) or not set(keys) <= allowed
            or any(not {'ts_code', 'trade_date'} <= set(row) <= fields for row in rows)
            or any(not is_a_share(code) or iso(day).year < 2023 for code, day in keys)):
        raise ValueError('GAP_INSERT_TARGET_INVALID')
    return keys


def _issue(table, key):
    return dict(table=table, ts_code=key[0], trade_date=key[1])


class GapImportRepository:
    def __init__(self, session_factory, *, owner_guard=None):
        self.session_factory, self.owner_guard = session_factory, owner_guard

    def insert_batch(self, table, rows, *, allowed_keys, schema):
        keys = validate_targets(table, rows, allowed_keys)
        if not rows or len(rows) > 500:
            raise ValueError('GAP_INSERT_BATCH_INVALID')
        types = schema.get('types', schema)
        values = [{field: sql_value(value, types.get(field, '')) for field, value in row.items()} for row in rows]
        result = dict(inserted=0, already_present=0, conflicts=[], owned_insert_keys=[])
        failed = False
        with self.session_factory() as session:
            try:
                if session.get_bind().dialect.name == 'sqlite':
                    session.connection().exec_driver_sql('BEGIN')
                if self.owner_guard is not None:
                    self.owner_guard(session)
                existing = PreparationImportRepository._read_targets(session, table, rows)
                for key, candidate in zip(keys, values):
                    old = existing.get(key)
                    if old is None:
                        columns = ','.join('`'+field+'`' for field in candidate)
                        params = ','.join(':'+field for field in candidate)
                        try:
                            with session.begin_nested():
                                inserted = session.execute(text(f'INSERT INTO `{table}` ({columns}) VALUES ({params})'), candidate)
                                if inserted.rowcount != 1:
                                    raise RuntimeError('GAP_INSERT_ROWCOUNT_INVALID')
                        except IntegrityError as error:
                            code = error.orig.args[0] if error.orig.args else None
                            duplicate = (code == 1062 or session.get_bind().dialect.name == 'sqlite'
                                and getattr(error.orig, 'sqlite_errorcode', None) in (2067, 1555))
                            if not duplicate:
                                raise
                            old = PreparationImportRepository._read_targets(session, table, [candidate]).get(key)
                            if old is None:
                                raise RuntimeError('GAP_CONCURRENT_KEY_DISAPPEARED') from None
                        else:
                            result['inserted'] += 1
                            result['owned_insert_keys'].append(list(key))
                            continue
                    result['already_present'] += 1
                    if not equal_business(old, candidate, list(candidate), types):
                        result['conflicts'].append(_issue(table, key))
                if self.owner_guard is not None:
                    self.owner_guard(session)
                session.commit()
            except SQLAlchemyError:
                session.rollback()
                failed = True
            except Exception:
                session.rollback()
                raise
        # Raise after the handler/session exit, without attaching SQL parameters
        # or driver exceptions (which may contain credentials/provider values).
        if failed:
            raise RuntimeError('GAP_DATABASE_FAILURE')
        return result

    def verify_targets(self, table, candidates, *, owned_insert_keys, schema):
        keys = validate_targets(table, candidates, [(r['ts_code'], r['trade_date']) for r in candidates])
        owned = {tuple(key) for key in owned_insert_keys}
        if not owned <= set(keys):
            raise ValueError('GAP_OWNED_KEYS_INVALID')
        missing, conflicts = [], []
        types = schema.get('types', schema)
        for offset in range(0, len(candidates), 500):
            rows = candidates[offset:offset+500]
            failed = False
            with self.session_factory() as session:
                try:
                    if self.owner_guard is not None:
                        self.owner_guard(session)
                    current = PreparationImportRepository._read_targets(session, table, rows)
                    for candidate in rows:
                        key = candidate['ts_code'], candidate['trade_date']
                        old = current.get(key)
                        if old is None:
                            missing.append(_issue(table, key))
                        elif not equal_business(old, candidate, list(candidate), types):
                            conflicts.append(_issue(table, key))
                except SQLAlchemyError:
                    failed = True
            if failed:
                raise RuntimeError('GAP_DATABASE_FAILURE')
        return dict(missing=missing, conflicts=conflicts)
