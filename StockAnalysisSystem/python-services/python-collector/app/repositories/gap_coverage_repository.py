"""Bounded keyset scans: coverage is row existence, not a price audit."""
from sqlalchemy import bindparam, inspect, text
from app.preparation.contracts import iso
from app.nightly.contracts import is_a_share

TABLES = ('stock_daily', 'stock_daily_basic')


def core_table(table):
    if table not in TABLES:
        raise ValueError('GAP_TABLE_INVALID')
    return table


class GapCoverageRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def iter_keys(self, table, codes, start, end, *, page_size=2000):
        core_table(table)
        if (not codes or len(codes) != len(set(codes)) or any(not is_a_share(c) for c in codes)
                or iso(start) > iso(end) or type(page_size) is not int or not 1 <= page_size <= 10000):
            raise ValueError('GAP_SCAN_INVALID')
        # Bound IN lists as well as result pages. Close the session before yielding
        # a page, so planning never holds a long-lived repeatable-read transaction.
        ordered = sorted(codes)
        for offset in range(0, len(ordered), 500):
            group = ordered[offset:offset+500]
            cursor = None
            while True:
                after = '' if cursor is None else (
                    ' AND (ts_code > :last_code OR (ts_code = :last_code AND trade_date > :last_day))')
                query = text(f'SELECT ts_code,trade_date FROM `{table}` WHERE ts_code IN :codes '
                    f'AND trade_date BETWEEN :start AND :end{after} ORDER BY ts_code,trade_date LIMIT :limit'
                ).bindparams(bindparam('codes', expanding=True))
                params = dict(codes=group, start=start, end=end, limit=page_size)
                if cursor is not None:
                    params.update(last_code=cursor[0], last_day=cursor[1])
                with self.session_factory() as session:
                    page = [(row[0], iso(str(row[1])).isoformat())
                            for row in session.execute(query, params).all()]
                if not page:
                    break
                if cursor is not None and page[0] <= cursor:
                    raise ValueError('GAP_SCAN_ORDER_INVALID')
                yield from page
                cursor = page[-1]
                if len(page) < page_size:
                    break

    def schema(self, table):
        core_table(table)
        with self.session_factory() as session:
            meta = inspect(session.connection())
            columns = meta.get_columns(table)
            unique = [meta.get_pk_constraint(table).get('constrained_columns', [])]
            unique.extend(item['column_names'] for item in meta.get_unique_constraints(table))
            unique.extend(item['column_names'] for item in meta.get_indexes(table) if item.get('unique'))
            return dict(columns=[item['name'] for item in columns],
                types={item['name']: str(item['type']).lower().replace(' ', '') for item in columns},
                unique_keys=[value for value in unique if value])
