"""Explicit local feature-store operations; importing never connects or builds."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


def main(argv=None):
    parser=argparse.ArgumentParser(description='Versioned causal traditional feature store')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate',help='Explicit metadata schema installation')
    build=sub.add_parser('build',help='Snapshot and compute; does NOT publish or train')
    build.add_argument('--start-date',required=True)
    build.add_argument('--cutoff',required=True)
    build.add_argument('--output')
    for command in ('publish','rollback'):
        sub.add_parser(command).add_argument('--build-id',required=True)
    sub.add_parser('inspect',help='Read active publication identity; no repair')
    args=parser.parse_args(argv)
    # Deliberately after argument parsing: --help is usable without credentials.
    from database.db_connector import DatabaseConnector
    from database.feature_store_repository import FeatureStoreRepository
    with DatabaseConnector(diagnostic=True) as db:
        repository=FeatureStoreRepository(db)
        if args.command=='migrate':
            repository.migrate()
            print(json.dumps(dict(schema='stock_feature_build',installed=True)))
            return 0
        if args.command in ('publish','rollback'):
            operation=repository.publish_build if args.command=='publish' else repository.rollback_publication
            print(json.dumps(operation(args.build_id)));return 0
        if args.command=='inspect':
            from database.feature_store_reader import metadata_row,validate_metadata,validate_table
            from data_processor.feature_store_contract import CANONICAL_TABLE
            with db.engine.connect() as connection:
                meta=metadata_row(connection);validate_metadata(meta)
                validate_table(connection,CANONICAL_TABLE,meta['build_id'])
                print(json.dumps({k:str(meta[k]) for k in ('build_id','state','source_start','source_cutoff','row_count','stock_count','storage_contract','algorithm_version')}))
            return 0
        from data_processor.feature_store_builder import snapshot_source,compute_build
        project=Path(__file__).resolve().parents[3]
        app=Path(__file__).resolve().parents[1]
        root=Path(args.output or os.getenv('FEATURE_STORE_ROOT','.runtime/feature_store'))
        root=(root if root.is_absolute() else project/root).resolve()
        protected=(app/'models').resolve()
        if root.is_relative_to(protected) or protected.is_relative_to(root):
            raise ValueError('Feature output must not overlap protected models')
        build_id=uuid4().hex
        folder=root/build_id/'source'
        manifest=snapshot_source(db,folder,args.start_date,args.cutoff)
        report=compute_build(db,manifest,build_id,directory=folder,repository=repository,
                             on_progress=lambda v:print(json.dumps(v),flush=True))
        print(json.dumps(report,ensure_ascii=False),flush=True)
        return 0


if __name__=='__main__':
    raise SystemExit(main())
