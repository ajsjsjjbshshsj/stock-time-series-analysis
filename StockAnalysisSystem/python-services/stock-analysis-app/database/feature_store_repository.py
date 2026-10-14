"""Explicit build repository using the configured DatabaseConnector only."""
from pathlib import Path
import json
import re
from sqlalchemy import inspect, text
from sqlalchemy import types

from data_processor.feature_store_contract import (STORAGE_CONTRACT,FEATURE_COLUMNS,
    build_table_name,columns_digest,validate_build_id)
from database.feature_store_schema import table_ddl


class FeatureStoreRepository:
    def __init__(self,db):
        self.db=db

    def migrate(self):
        """Never invoked from module imports or read/model-loading paths."""
        project=Path(__file__).resolve().parents[3]
        sql=(project/'infrastructure/mysql/migrations/V0.9_003_feature_store_builds.sql').read_text(encoding='utf-8')
        with self.db.engine.begin() as connection:
            connection.exec_driver_sql(sql)
        schema=inspect(self.db.engine)
        columns={c['name']:c for c in schema.get_columns('stock_feature_build')}
        required={'build_id','storage_contract','algorithm_version','columns_sha256','code_sha256',
                  'source_sha256','output_sha256','source_start','source_cutoff','row_count',
                  'stock_count','state','active_slot','created_at','verified_at','failure_code','source_manifest'}
        if set(columns)!=required or schema.get_pk_constraint('stock_feature_build')['constrained_columns']!=['build_id']:
            raise ValueError('Feature build metadata schema mismatch')
        definitions={
            'build_id':(types.CHAR,32,False), 'storage_contract':(types.VARCHAR,64,False),
            'algorithm_version':(types.VARCHAR,32,False),
            'columns_sha256':(types.CHAR,64,False),'code_sha256':(types.CHAR,64,False),
            'source_sha256':(types.CHAR,64,True),'output_sha256':(types.CHAR,64,True),
            'source_start':(types.DATE,None,False),'source_cutoff':(types.DATE,None,False),
            'row_count':(types.BIGINT,None,False),'stock_count':(types.BIGINT,None,False),
            'state':(types.VARCHAR,16,False),'active_slot':(types.VARCHAR,32,True),
            'created_at':(types.DATETIME,None,False),'verified_at':(types.DATETIME,None,True),
            'failure_code':(types.VARCHAR,64,True),
            'source_manifest':(types.JSON,None,False),
        }
        for name,(kind,length,nullable) in definitions.items():
            column=columns[name]
            if (not isinstance(column['type'],kind) or column['nullable']!=nullable
                    or (length is not None and column['type'].length!=length)
                    or (name in ('row_count','stock_count') and not column['type'].unsigned)):
                raise ValueError('Feature build metadata schema type mismatch')
        uniques=schema.get_unique_constraints('stock_feature_build')
        if not any(u['column_names']==['active_slot'] for u in uniques):
            raise ValueError('Feature active-slot uniqueness missing')
        def normalize(expression):
            return re.sub(r'[\s`()]+','',expression).replace('_utf8mb4','').lower()
        checks={c['name']:normalize(c['sqltext']) for c in schema.get_check_constraints('stock_feature_build')}
        expected={
            'ck_feature_build_state':normalize("state IN ('BUILDING','READY','FAILED')"),
            'ck_feature_active_ready':normalize("active_slot IS NULL OR state='READY'"),
            'ck_feature_source_range':normalize('source_start<=source_cutoff'),
        }
        if any(checks.get(name)!=expression for name,expression in expected.items()):
            raise ValueError('Feature build metadata schema checks mismatch')

    def start_build(self,build_id,manifest,algorithm_version,code_sha256):
        table=build_table_name(build_id)
        with self.db.engine.begin() as connection:
            connection.execute(text('''INSERT INTO stock_feature_build
                (build_id,storage_contract,algorithm_version,columns_sha256,code_sha256,
                 source_sha256,source_manifest,source_start,source_cutoff,row_count,stock_count,state)
                VALUES (:build_id,:contract,:algorithm,:columns,:code,:source,:manifest,:start,:cutoff,0,0,'BUILDING')'''),
                dict(build_id=build_id,contract=STORAGE_CONTRACT,algorithm=algorithm_version,
                     columns=columns_digest(),code=code_sha256,source=manifest['source_sha256'],
                     manifest=json.dumps(manifest,ensure_ascii=False,allow_nan=False),
                     start=manifest['source_start'],cutoff=manifest['source_cutoff']))
        try:
            with self.db.engine.begin() as connection:
                connection.exec_driver_sql(table_ddl(table))
        except Exception:
            self.fail_build(build_id,'STAGING_DDL_FAILED')
            raise ValueError('Staging table creation failed') from None

    def insert_build_rows(self,build_id,rows):
        if not rows:return 0
        table=build_table_name(build_id)
        names=('build_id','ts_code','trade_date',*FEATURE_COLUMNS)
        if len(rows)>1000 or any(tuple(r)!=names or r['build_id']!=build_id for r in rows):
            raise ValueError('Uncontrolled staging insert shape')
        fields=','.join('`'+c+'`' for c in names)
        binds=','.join(':'+c for c in names)
        with self.db.engine.begin() as connection:
            state=connection.execute(text('SELECT state FROM stock_feature_build WHERE build_id=:id'),dict(id=build_id)).scalar_one()
            if state!='BUILDING':raise ValueError('Build is not writable')
            result=connection.execute(text(f'INSERT INTO `{table}` ({fields}) VALUES ({binds})'),rows)
            return result.rowcount

    def finish_build(self,build_id,report):
        table=build_table_name(build_id)
        with self.db.engine.begin() as connection:
            count=connection.execute(text(f'SELECT COUNT(*) FROM `{table}` WHERE build_id=:id'),dict(id=build_id)).scalar_one()
            if count!=report['row_count']:raise ValueError('Persisted feature row count mismatch')
            result=connection.execute(text('''UPDATE stock_feature_build SET row_count=:rows,
                stock_count=:stocks,output_sha256=:output WHERE build_id=:id AND state='BUILDING' '''),
                dict(rows=report['row_count'],stocks=report['stock_count'],output=report['output_sha256'],id=build_id))
            if result.rowcount!=1:raise ValueError('Build completion identity mismatch')

    def fail_build(self,build_id,code):
        validate_build_id(build_id)
        if code not in ('FEATURE_BUILD_FAILED','STAGING_DDL_FAILED'):raise ValueError('Unsafe build failure code')
        with self.db.engine.begin() as connection:
            connection.execute(text("UPDATE stock_feature_build SET state='FAILED',failure_code=:code WHERE build_id=:id AND state='BUILDING' AND active_slot IS NULL"),dict(code=code,id=build_id))

    def publication_lock_name(self):
        from database.feature_store_publication import lock_name
        return lock_name(self.db)

    def publish_build(self,build_id):
        from database.feature_store_publication import publish
        return publish(self.db,build_id)

    def rollback_publication(self,build_id):
        from database.feature_store_publication import publish
        return publish(self.db,build_id,rollback=True)

    def load_feature_snapshot(self,session,stock_codes=None,start_date=None,end_date=None,expected_keys=None):
        from database.feature_store_reader import load_feature_snapshot
        return load_feature_snapshot(session,stock_codes,start_date,end_date,expected_keys)
