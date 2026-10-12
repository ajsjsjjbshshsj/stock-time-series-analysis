"""Insert missing stock identities from an already verified market snapshot.

Existing names and industries remain untouched; an incompatible IPO identity
is a data conflict, not an excuse to overwrite the original record.
"""
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.preparation.service import PreparationFailure
from .contracts import validate_market


class SnapshotReferenceWriter:
    def __init__(self,session_factory):
        self.session_factory=session_factory

    def ensure_from_snapshot(self,snapshot,codes,*,owner_guard):
        validate_market(snapshot)
        if len(codes)!=len(set(codes)) or not set(codes)<=set(snapshot['codes']):
            raise ValueError('REFERENCE_OUTSIDE_MARKET')
        members={r['ts_code']:r for r in snapshot['members']}
        with self.session_factory.begin() as session:
            # sqlite's legacy driver otherwise commits the first SAVEPOINT
            # before the outer transaction begins (isolated SQL tests).
            if session.get_bind().dialect.name=='sqlite':
                session.connection().exec_driver_sql('BEGIN')
            owner_guard(session)
            suffix=' FOR UPDATE' if session.get_bind().dialect.name=='mysql' else ''
            statement=text('SELECT list_date FROM stock_basic WHERE ts_code=:code'+suffix)
            for code in sorted(codes):
                member=members[code]
                old=session.execute(statement,dict(code=code)).first()
                if old is not None:
                    self._same_ipo(old,member)
                    continue
                row={k:member[k] for k in ('ts_code','symbol','name','area','industry','list_date')}
                try:
                    with session.begin_nested():
                        session.execute(text('INSERT INTO stock_basic (ts_code,symbol,name,area,industry,list_date) '
                            'VALUES (:ts_code,:symbol,:name,:area,:industry,:list_date)'),row)
                except IntegrityError as error:
                    duplicate=(error.orig.args and error.orig.args[0]==1062) or (
                        session.get_bind().dialect.name=='sqlite' and
                        getattr(error.orig,'sqlite_errorcode',None) in (2067,1555))
                    if not duplicate:raise
                    old=session.execute(statement,dict(code=code)).first()
                    self._same_ipo(old,member)

    @staticmethod
    def _same_ipo(old,member):
        if old is None or old[0] is None or str(old[0])!=member['list_date']:
            raise PreparationFailure('INVALID_EXISTING_DATA')
