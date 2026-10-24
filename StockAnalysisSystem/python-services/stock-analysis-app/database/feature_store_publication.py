"""Explicit publication with an owned lock and recoverable DDL/DML boundary."""
import hashlib
from contextlib import contextmanager

from sqlalchemy import text,inspect

from data_processor.feature_store_contract import (ACTIVE_SLOT,CANONICAL_TABLE,
    FEATURE_COLUMNS,build_table_name,archive_table_name,validate_build_id)
from data_processor.feature_store_source import canonical_bytes,source_frames,validate_source,update_source_digest
from database.feature_store_reader import metadata_row,validate_metadata,validate_table,FeatureStoreUnavailable


def lock_name(db):
    return 'feature-store-v2:'+hashlib.sha256(db.engine.url.database.encode()).hexdigest()[:40]


@contextmanager
def owned_lock(db):
    with db.engine.connect() as connection:
        name=lock_name(db)
        acquired=connection.execute(text('SELECT GET_LOCK(:name,0)'),dict(name=name)).scalar_one()==1
        connection.commit()
        if not acquired:raise FeatureStoreUnavailable('Feature publisher is already locked')
        try:yield connection
        finally:
            connection.rollback()
            connection.execute(text('SELECT RELEASE_LOCK(:name)'),dict(name=name))
            connection.commit()


def active_metadata(connection):
    row=connection.execute(text('SELECT * FROM stock_feature_build WHERE active_slot=:slot'),dict(slot=ACTIVE_SLOT)).mappings().one_or_none()
    return dict(row) if row else None


def verify_build(connection,meta,table):
    validate_metadata(meta);validate_table(connection,table,meta['build_id'])
    output=hashlib.sha256();source=hashlib.sha256();count=0;stocks=0
    names=('build_id','ts_code','trade_date',*FEATURE_COLUMNS)
    fields=','.join('`'+c+'`' for c in names)
    # One stock in memory, all values and keys checked, not a sampling shortcut.
    for raw in source_frames(connection,meta['source_start'],meta['source_cutoff']):
        raw=validate_source(raw,meta['source_start'],meta['source_cutoff'])
        code=raw.ts_code.iloc[0];update_source_digest(source,raw)
        rows=connection.execute(text(f'SELECT {fields} FROM `{table}` WHERE ts_code=:code ORDER BY trade_date'),dict(code=code)).mappings().all()
        if list(zip(raw.ts_code,raw.trade_date))!=[(r['ts_code'],r['trade_date']) for r in rows]:
            raise FeatureStoreUnavailable('Staging source key coverage differs')
        for row in rows:output.update(canonical_bytes(dict(row))+b'\n')
        count+=len(rows);stocks+=1
    total=connection.execute(text(f'SELECT COUNT(*) FROM `{table}`')).scalar_one()
    if (count!=total or count!=meta['row_count'] or stocks!=meta['stock_count']
            or source.hexdigest()!=meta['source_sha256'] or output.hexdigest()!=meta['output_sha256']):
        raise FeatureStoreUnavailable('Staging source/output proof mismatch')


def rename_pair(connection,incoming,previous):
    if previous:
        archive=archive_table_name(previous['build_id'])
        if inspect(connection).has_table(archive):raise FeatureStoreUnavailable('Archive already exists; refusing overwrite')
        validate_table(connection,CANONICAL_TABLE,previous['build_id'])
        connection.exec_driver_sql(f'RENAME TABLE `{CANONICAL_TABLE}` TO `{archive}`, `{incoming}` TO `{CANONICAL_TABLE}`')
    else:
        if inspect(connection).has_table(CANONICAL_TABLE):raise FeatureStoreUnavailable('Unowned canonical feature table')
        connection.exec_driver_sql(f'RENAME TABLE `{incoming}` TO `{CANONICAL_TABLE}`')
    connection.commit()


def restore_pair(connection,incoming,previous,candidate):
    # Never rename unknown tables after a failure; operator must resolve those.
    validate_table(connection,CANONICAL_TABLE,candidate)
    if inspect(connection).has_table(incoming):raise FeatureStoreUnavailable('Recovery target already exists')
    if previous:
        archive=archive_table_name(previous['build_id'])
        validate_table(connection,archive,previous['build_id'])
        connection.exec_driver_sql(f'RENAME TABLE `{CANONICAL_TABLE}` TO `{incoming}`, `{archive}` TO `{CANONICAL_TABLE}`')
    else:
        connection.exec_driver_sql(f'RENAME TABLE `{CANONICAL_TABLE}` TO `{incoming}`')
    connection.commit()


def publish(db,build_id,rollback=False,*,managed=None):
    validate_build_id(build_id)
    with owned_lock(db) as connection:
        incoming=archive_table_name(build_id) if rollback else build_table_name(build_id)
        if managed is not None:
            managed.owner_guard()
            managed.recover(connection,incoming)
        meta=metadata_row(connection,build_id)
        required='READY' if rollback else 'BUILDING'
        previous=active_metadata(connection)
        if managed is not None and previous and previous['build_id']==build_id:
            # A lost commit acknowledgement is reconciled, never re-RENAME'd.
            verify_build(connection,meta,CANONICAL_TABLE);connection.rollback()
            managed.owner_guard();managed.prepare();managed.finish('COMMITTED')
            return dict(build_id=build_id,published=True,reconciled_commit=True)
        if meta['state']!=required or meta['active_slot'] is not None:
            raise FeatureStoreUnavailable('Candidate is not an inactive eligible build')
        if managed is not None:managed.require_previous(previous)
        # Consistent snapshot, no raw writes. Named lock spans validation + DDL.
        connection.commit()
        connection.exec_driver_sql('START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
        verify_build(connection,meta,incoming)
        connection.rollback()
        if managed is not None:
            managed.owner_guard();managed.prepare()
        try:
            if managed is not None:managed.owner_guard()
            rename_pair(connection,incoming,previous)
            connection.commit()
            with connection.begin():
                connection.execute(text('UPDATE stock_feature_build SET active_slot=NULL WHERE active_slot=:slot'),dict(slot=ACTIVE_SLOT))
                result=connection.execute(text("UPDATE stock_feature_build SET state='READY',active_slot=:slot,verified_at=CURRENT_TIMESTAMP WHERE build_id=:id AND state=:state AND active_slot IS NULL"),dict(slot=ACTIVE_SLOT,id=build_id,state=required))
                if result.rowcount!=1:raise FeatureStoreUnavailable('Activation state changed')
            if managed is not None:managed.finish('COMMITTED')
            return dict(build_id=build_id,published=True,previous_build_id=previous['build_id'] if previous else None)
        except Exception:
            connection.rollback()
            # Resolve ambiguous commit before touching physical state.
            current=active_metadata(connection);connection.commit()
            if current and current['build_id']==build_id and current['state']=='READY':
                validate_table(connection,CANONICAL_TABLE,build_id)
                if managed is not None:managed.finish('COMMITTED')
                return dict(build_id=build_id,published=True,reconciled_commit=True)
            if (current and (not previous or current['build_id']!=previous['build_id'])) or (previous and not current):
                raise FeatureStoreUnavailable('Publication recovery requires operator inspection') from None
            # RENAME can succeed while its acknowledgement/commit raises. Infer
            # physical state under the *same still-owned* lock, never a flag set
            # after a network acknowledgement that might not arrive.
            if connection.execute(text('SELECT IS_USED_LOCK(:name)=CONNECTION_ID()'),dict(name=lock_name(db))).scalar_one()!=1:
                raise FeatureStoreUnavailable('Publication lock lost; operator recovery required') from None
            schema=inspect(connection)
            if not schema.has_table(incoming):
                restore_pair(connection,incoming,previous,build_id)
            else:
                validate_table(connection,incoming,build_id)
                if previous:validate_table(connection,CANONICAL_TABLE,previous['build_id'])
                elif schema.has_table(CANONICAL_TABLE):
                    raise FeatureStoreUnavailable('Unknown physical publication state; operator recovery required') from None
            if managed is not None:managed.finish('ABORTED')
            if not rollback and managed is None:
                connection.execute(text("UPDATE stock_feature_build SET state='FAILED',failure_code='PUBLICATION_FAILED' WHERE build_id=:id AND state='BUILDING' AND active_slot IS NULL"),dict(id=build_id))
                connection.commit()
            raise FeatureStoreUnavailable('Publication failed; previous dataset retained, evidence preserved') from None
