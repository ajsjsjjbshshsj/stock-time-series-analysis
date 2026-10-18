"""Explicit task migration and independent worker. No import-time side effects."""
import argparse
from getpass import getpass
import os
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('migrate',help='Create only training tables, never market data')
    commands.add_parser('migrate-all-market',help='Explicit additive training queue upgrade; stop API/worker first')
    run=commands.add_parser('run',help='Run the single-slot worker, protected mode required')
    run.add_argument('--once',action='store_true',help='Attempt at most one queued job')
    commands.add_parser('status',help='Show execution slot status; never starts training')
    commands.add_parser('recover',help='Explicit admin attestation after original process tree stopped')
    args=parser.parse_args()
    from api.auth.contracts import AuthSettings
    settings=AuthSettings.from_env()
    from database.db_connector import DatabaseConnector
    from tasks.repository import TaskRepository
    from tasks.service import TaskService
    from tasks.source import SourceProvider,PROJECT
    from tasks.contracts import encoded
    with DatabaseConnector(diagnostic=True) as db:
        repo=TaskRepository(db.engine)
        if args.command=='migrate-all-market':
            repo.migrate_all_market();print('All-market training queue schema ready; no task started.');return
        if args.command=='migrate':
            repo.migrate();print('Training tables ready; no task or worker started.');return
        if settings.mode!='protected':raise ValueError('Protected configuration required')
        service=TaskService(repo,SourceProvider(db.engine))
        if args.command=='status':print(encoded(service.slot_status()));return
        if args.command=='recover':
            slot=service.slot_status()
            if not slot['quarantined']:raise ValueError('No quarantined execution')
            print('Verify on the ORIGINAL machine that the old worker and ALL owned descendants have stopped.')
            phrase=f"STOPPED {slot['task_id']} {slot['worker_id']} {slot['generation']}"
            print('Only after verifying shutdown, type exactly: '+phrase)
            if input('Confirmation: ')!=phrase:raise ValueError('Recovery not confirmed')
            from api.auth.repository import AuthRepository
            from api.auth.service import AuthService
            auth=AuthService(AuthRepository(db.engine),secret=settings.secret)
            username=input('Administrator username: ');password=getpass('Administrator password: ')
            login=auth.login(username,password,'local-training-recovery')
            try:service.recover(login['account']['id'],slot['task_id'],slot['worker_id'],slot['generation'],operator_confirmed=True)
            finally:auth.logout(login['token'])
            print('Execution slot recovered; interrupted task was NOT requeued.');return
        from tasks.worker import Worker
        from tasks.artifacts import task_path
        root=Path(os.getenv('TRAINING_ARTIFACT_ROOT','.runtime/training_tasks/candidates'))
        if not root.is_absolute():root=PROJECT/root
        task_path(root,'0'*32)
        worker=Worker(service,root)
        if args.once:worker.run_once()
        else:worker.run()


if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:raise SystemExit(0)
    except Exception:
        print('Training worker operation failed; check configuration, explicit migration and slot status.',file=sys.stderr)
        raise SystemExit(1)
