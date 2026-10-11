from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from preparation_store_support import database, load


def ref(**changes):
    return dict(dict(kind='adj_factor', ts_code='000001.SZ', start_date='2026-09-28',
        end_date='2026-09-30', provider='tushare.adj_factor', relative_path='bundles/a/manifest.json',
        manifest_sha256='a'*64, content_sha256='b'*64), **changes)


def test_evidence_index_is_idempotent_and_intersects_shared_stock_ranges():
    cls = load('source_evidence_repository', 'SourceEvidenceRepository')
    with database() as factory:
        repo = cls(factory)
        first = repo.register(ref())
        assert repo.register(ref()) == first
        assert repo.list('adj_factor', ['000001.SZ'], '2026-09-29', '2026-10-01') == [first]
        assert repo.list('adj_factor', ['600000.SH'], '2026-09-29', '2026-10-01') == []


@pytest.mark.parametrize('changes', [dict(relative_path='../secret'), dict(relative_path='C:/secret'),
    dict(content_sha256='invalid'), dict(start_date='2026-10-01'), dict(ts_code='bad'), dict(kind='model')])
def test_evidence_reference_rejects_untrusted_paths_and_identity(changes):
    cls = load('source_evidence_repository', 'SourceEvidenceRepository')
    with database() as factory:
        with pytest.raises(ValueError): cls(factory).register(ref(**changes))
