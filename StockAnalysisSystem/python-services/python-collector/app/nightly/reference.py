"""Insert missing stock identities from an already verified market snapshot.

Existing names and industries remain untouched; an incompatible IPO identity
is a data conflict, not an excuse to overwrite the original record.
"""
from app.preparation.service import PreparationFailure
from app.repositories.stock_reference import FIELDS, insert_missing_reference, ReferenceInsertConflict
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
            owner_guard(session)
            for code in sorted(codes):
                member=members[code]
                row={k:member[k] for k in FIELDS}
                try:
                    old=insert_missing_reference(session,row)
                except ReferenceInsertConflict:
                    raise PreparationFailure('INVALID_EXISTING_DATA') from None
                if old is not None:
                    self._same_ipo(old,member)

    @staticmethod
    def _same_ipo(old,member):
        if old is None or old[0] is None or str(old[0])!=member['list_date']:
            raise PreparationFailure('INVALID_EXISTING_DATA')
