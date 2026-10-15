"""Explicit local account administration. Never accepts passwords in arguments."""
import argparse
from getpass import getpass
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['migrate','bootstrap'])
    args=parser.parse_args()
    # Reuse portable environment/.env discovery; never print configured secrets.
    from database.db_connector import DatabaseConnector
    from api.auth.repository import AuthRepository
    from api.auth.service import AuthService
    from api.auth.contracts import AuthSettings
    with DatabaseConnector(diagnostic=True) as db:
        repo=AuthRepository(db.engine)
        if args.command=='migrate':
            repo.migrate()
            print('Authentication tables ready; no accounts created.')
            return
        settings=AuthSettings.from_env()
        if settings.mode!='protected': raise ValueError('Set protected auth configuration first')
        name=input('Administrator username: ')
        first=getpass('New password (12-128 characters): ')
        second=getpass('Confirm password: ')
        if first!=second: raise ValueError('Password confirmation mismatch')
        AuthService(repo,secret=settings.secret).bootstrap(name,first)
        print('Initial administrator created. No password or session is printed.')


if __name__=='__main__':
    try: main()
    except Exception:
        # Configuration parsers can raise ValueError containing configured secrets.
        # Never print arbitrary exception text, including apparent domain errors.
        print('Account operation failed; check configuration and explicit migration.',file=sys.stderr)
        raise SystemExit(1)
