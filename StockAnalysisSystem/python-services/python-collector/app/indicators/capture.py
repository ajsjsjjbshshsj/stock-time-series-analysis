"""Explicit additive transaction capture; run only after backup/write quiescence."""
import re
from sqlalchemy import inspect,text,UniqueConstraint
from .outbox import metadata

FIELDS=('ts_code','trade_date','open','high','low','close','pre_close','change','pct_chg','vol','amount')
PREFIX='stock_daily_indicator_'


def _normal(sql):return re.sub(r'\s+',' ',sql).strip().rstrip(';')


def _metadata_compatible(schema, dialect, *, tables=None):
    def type_name(kind):
        name=_normal(kind.compile(dialect=dialect)).upper()
        return 'TINYINT(1)' if dialect.name=='mysql' and name in ('BOOL','BOOLEAN') else name
    for table in metadata.tables.values() if tables is None else tables:
        actual={column['name']:column for column in schema.get_columns(table.name)}
        if (set(actual)!=set(table.c.keys())
                or schema.get_pk_constraint(table.name)['constrained_columns']!=[c.name for c in table.primary_key]):
            raise ValueError('Existing indicator metadata schema drift')
        for column in table.c:
            found=actual[column.name]
            if (found['nullable']!=column.nullable
                    or type_name(found['type'])!=type_name(column.type.dialect_impl(dialect))):
                raise ValueError('Existing indicator metadata column drift')
        indexes={tuple(i['column_names']) for i in schema.get_indexes(table.name) if not i['unique']}
        if not {tuple(c.name for c in index.columns) for index in table.indexes}.issubset(indexes):
            raise ValueError('Existing indicator metadata index drift')
        unique={tuple(i['column_names']) for i in schema.get_unique_constraints(table.name)}
        if not {tuple(c.name for c in constraint.columns) for constraint in table.constraints
                if isinstance(constraint,UniqueConstraint)}.issubset(unique):
            raise ValueError('Existing indicator metadata uniqueness drift')


def source_scope(dialect, ref):
    """The exact captured A-share domain, also used by snapshot readers."""
    if ref not in ('NEW', 'OLD', 'd', 'o'):
        raise ValueError('Invalid source alias')
    code=f'{ref}.ts_code'
    if dialect=='mysql':
        valid=f"CONVERT({code} USING utf8mb4) COLLATE utf8mb4_bin REGEXP '^((60|68)[0-9]{{4}}[.]SH|(0[0-9]{{5}}|30[0-9]{{4}})[.]SZ)$'"
    elif dialect=='sqlite':
        valid=(f"length({code})=9 AND substr({code},1,6) NOT GLOB '*[^0-9]*' AND "
            f"((substr({code},7)='.SH' AND substr({code},1,2) IN ('60','68')) OR "
            f"(substr({code},7)='.SZ' AND (substr({code},1,1)='0' OR substr({code},1,2)='30')))" )
    else:
        raise ValueError('Unsupported capture database')
    return f"({valid}) AND {ref}.trade_date>='2023-01-01'"


def definitions(dialect):
    if dialect not in ('sqlite','mysql'):raise ValueError('Unsupported capture database')
    scope=lambda ref:source_scope(dialect,ref)
    def emit(ref,operation,condition):
        day=f"DATE_FORMAT({ref}.trade_date,'%Y-%m-%d')" if dialect=='mysql' else f"strftime('%Y-%m-%d',{ref}.trade_date)"
        values=','.join(f"'{key}',"+(day if key=='trade_date' else f'{ref}.`{key}`') for key in FIELDS)
        payload=f'JSON_OBJECT({values})'
        stamp='UNIX_TIMESTAMP()' if dialect=='mysql' else "CAST(strftime('%s','now') AS INTEGER)"
        return ("INSERT INTO indicator_outbox(ts_code,trade_date,operation,payload,captured_at) "
            f"SELECT {ref}.ts_code,{day},'{operation}',{payload},{stamp} WHERE {condition};")
    def unequal(key):
        if key=='ts_code':
            return ('NOT (BINARY OLD.`ts_code` <=> BINARY NEW.`ts_code`)' if dialect=='mysql'
                    else 'NOT (OLD.`ts_code` COLLATE BINARY IS NEW.`ts_code` COLLATE BINARY)')
        return f'NOT (OLD.`{key}` <=> NEW.`{key}`)' if dialect=='mysql' else f'NOT (OLD.`{key}` IS NEW.`{key}`)'
    moved='('+ ' OR '.join(unequal(k) for k in FIELDS[:2])+')'
    changed='('+ ' OR '.join(unequal(k) for k in FIELDS)+')'
    bodies={
        'insert':emit('NEW','UPSERT',scope('NEW')),
        'update':emit('OLD','DELETE',f'{scope("OLD")} AND {moved}')+emit('NEW','UPSERT',f'{scope("NEW")} AND {changed}'),
        'delete':emit('OLD','DELETE',scope('OLD')),
    }
    return {PREFIX+action+'_v1':dict(event=action.upper(),body='BEGIN '+body+' END') for action,body in bodies.items()}


def require_capture(connection):
    """Read-only installation check; never installs or repairs a live schema."""
    dialect=connection.dialect.name
    wanted=definitions(dialect)
    schema=inspect(connection)
    tables=set(schema.get_table_names())
    if not {'stock_daily', *metadata.tables}.issubset(tables):
        raise ValueError('Indicator capture installation required')
    columns=schema.get_columns('stock_daily')
    if not set(FIELDS).issubset(c['name'] for c in columns):
        raise ValueError('Compatible stock_daily required')
    _metadata_compatible(schema,connection.dialect)
    if dialect=='mysql':
        for table in ('stock_daily', *metadata.tables):
            storage=connection.execute(text('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name'),dict(name=table)).scalar_one()
            if storage.upper()!='INNODB':
                raise ValueError('Transactional indicator source and metadata required')
        rows={r['TRIGGER_NAME']:r for r in connection.execute(text('SELECT TRIGGER_NAME,EVENT_MANIPULATION,ACTION_TIMING,EVENT_OBJECT_TABLE,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()')).mappings()}
    else:
        rows={r['name']:r for r in connection.execute(text("SELECT name,sql FROM sqlite_master WHERE type='trigger'")).mappings()}
    for name,spec in wanted.items():
        row=rows.get(name)
        if row is None:
            raise ValueError('Indicator capture installation incomplete')
        if dialect=='mysql':
            valid=(row['EVENT_MANIPULATION']==spec['event'] and row['ACTION_TIMING']=='AFTER'
                and row['EVENT_OBJECT_TABLE']=='stock_daily' and _normal(row['ACTION_STATEMENT'])==_normal(spec['body']))
        else:
            valid=_normal(row['sql'])==_normal(f'CREATE TRIGGER {name} AFTER {spec["event"]} ON stock_daily FOR EACH ROW {spec["body"]}')
        if not valid:
            raise ValueError('Existing indicator capture trigger drift')
    return dict(columns=[dict(name=c['name'],type=str(c['type']),nullable=c['nullable']) for c in columns],triggers=wanted)


def migrate_capture(engine):
    """No activation/backfill. Inspect every trigger before creating any of them.

    MySQL trigger DDL commits implicitly; callers must quiesce source writers.
    An interrupted install is resumable, not advertised as atomic installation.
    """
    dialect=engine.dialect.name;wanted=definitions(dialect);schema=inspect(engine)
    if 'stock_daily' not in schema.get_table_names() or not set(FIELDS)<={c['name'] for c in schema.get_columns('stock_daily')}:
        raise ValueError('Compatible stock_daily required')
    with engine.connect() as c:
        if dialect=='mysql':
            kind=c.execute(text("SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='stock_daily'")).scalar_one()
            if kind.upper()!='INNODB':raise ValueError('Transactional source table required')
            existing={r['TRIGGER_NAME']:r for r in c.execute(text('SELECT TRIGGER_NAME,EVENT_MANIPULATION,ACTION_TIMING,EVENT_OBJECT_TABLE,ACTION_STATEMENT FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA=DATABASE()')).mappings()}
        else:existing={r['name']:r for r in c.execute(text("SELECT name,sql FROM sqlite_master WHERE type='trigger'")).mappings()}
    for name,spec in wanted.items():
        row=existing.get(name)
        if row is None:continue
        expected=f'CREATE TRIGGER {name} AFTER {spec["event"]} ON stock_daily FOR EACH ROW {spec["body"]}'
        if dialect=='mysql':
            valid=(row['EVENT_MANIPULATION']==spec['event'] and row['ACTION_TIMING']=='AFTER'
                and row['EVENT_OBJECT_TABLE']=='stock_daily' and _normal(row['ACTION_STATEMENT'])==_normal(spec['body']))
        else:valid=_normal(row['sql'])==_normal(expected)
        if not valid:raise ValueError('Existing indicator capture trigger drift')
    metadata.create_all(engine)
    _metadata_compatible(inspect(engine),engine.dialect)
    if dialect=='mysql':
        with engine.connect() as c:
            for table in metadata.tables.values():
                storage=c.execute(text('SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=:name'),dict(name=table.name)).scalar_one()
                if storage.upper()!='INNODB':raise ValueError('Transactional indicator metadata required')
    with engine.begin() as c:
        for name,spec in wanted.items():
            if name not in existing:
                # Compile DATE_FORMAT percent literals through the DB dialect;
                # raw driver SQL with empty parameters treats %Y as formatting.
                c.execute(text(f'CREATE TRIGGER {name} AFTER {spec["event"]} ON stock_daily FOR EACH ROW {spec["body"]}'))
