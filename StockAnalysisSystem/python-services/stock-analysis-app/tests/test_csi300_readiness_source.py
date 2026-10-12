from contextlib import contextmanager
import importlib
import json
import pandas as pd
import pytest
from tests.test_csi300_readiness import data


def module():
    try: return importlib.import_module('data_loader.csi300_readiness_source')
    except ModuleNotFoundError: pytest.fail('strict CSI300 source reader missing')


class DB:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    @contextmanager
    def session_scope(self): yield object()


def test_source_reader_uses_exact_pool_and_no_legacy_cache(monkeypatch):
    from database import repository
    pool, frame, basic, calendar = data(20)
    monkeypatch.setattr(repository, 'load_stock_basic_df', lambda s: basic)
    def daily(session, stock_codes, start_date, end_date):
        assert stock_codes == pool['codes']
        assert start_date == '2024-01-01'
        return frame
    monkeypatch.setattr(repository, 'load_daily_panel', daily)
    result = module().read_local_sources(pool, '2024-01-01', calendar['end'], connector=DB())
    assert len(result[0]) == 6000
    assert result[2] is None
    assert result[3]['source_mode'] == 'strict_database'
    assert len(result[3]['daily_sha256']) == 64


def test_db_failure_cannot_be_an_empty_ready_result(monkeypatch):
    from database import repository
    pool, frame, basic, calendar = data(20)
    monkeypatch.setattr(repository, 'load_stock_basic_df', lambda s: (_ for _ in ()).throw(RuntimeError('private db details')))
    with pytest.raises(ValueError, match='SOURCE_UNAVAILABLE'):
        module().read_local_sources(pool, '2024-01-01', calendar['end'], connector=DB())


def test_explicit_factor_manifest_hash_is_checked_before_db(tmp_path):
    pool, _, _, calendar = data(20)
    factor = tmp_path/'factors.parquet'
    pd.DataFrame(dict(ts_code=['000001.SZ'], trade_date=['2024-01-01'], adj_factor=[1.])).to_parquet(factor)
    proof = tmp_path/'factor_manifest.json'
    proof.write_text(json.dumps(dict(source='tushare.adj_factor', data_path=str(factor), data_sha256='0'*64,
                   codes=['000001.SZ'], start='2024-01-01', end='2024-01-01', complete=True)))
    class NoDB:
        def __enter__(self): pytest.fail('invalid factor evidence must reject before DB')
    with pytest.raises(ValueError):
        module().read_local_sources(pool, '2024-01-01', calendar['end'], connector=NoDB(), factor_manifest=proof)
