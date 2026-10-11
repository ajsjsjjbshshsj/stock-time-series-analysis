import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))

import pytest
from preparation_store_support import database,load


def cls():
    try: return importlib.import_module('app.preparation.evidence').EvidenceStore
    except ModuleNotFoundError: pytest.fail('shared evidence store not implemented')


def bundle():
    return dict(kind='adj_factor',provider='tushare.adj_factor',start_date='2026-09-28',end_date='2026-09-30',
        expected_codes=['000001.SZ'],rows=[dict(ts_code='000001.SZ',trade_date='2026-09-28',adj_factor=1.0)])


def test_registered_source_is_reused_by_stock_across_any_pool(tmp_path):
    store_cls=cls()
    with database() as factory:
        repo=load('source_evidence_repository','SourceEvidenceRepository')(factory)
        store=store_cls(tmp_path,repo)
        refs=store.register_verified(bundle())
        data=store.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')
        assert data['adj_factor']==[dict(ts_code='000001.SZ',trade_date='2026-09-28',adj_factor=1.0)]


def test_corrupt_source_file_not_reused_even_when_index_exists(tmp_path):
    store_cls=cls()
    with database() as factory:
        store=store_cls(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
        refs=store.register_verified(bundle())
        manifest=tmp_path/refs[0]['relative_path']
        manifest.write_text('{}',encoding='utf-8')
        with pytest.raises(ValueError):
            store.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')


def test_untrusted_path_refused_before_reading(tmp_path):
    store_cls=cls()
    with database() as factory:
        store=store_cls(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
        with pytest.raises(ValueError):
            store.load([dict(relative_path='../secret')],codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')


def test_second_stock_reference_cannot_bypass_cached_manifest_hash(tmp_path):
    store_cls=cls()
    with database() as factory:
        store=store_cls(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
        data=bundle(); data['expected_codes']=['000001.SZ','600000.SH']
        refs=store.register_verified(data)
        refs[1]=dict(refs[1],manifest_sha256='0'*64)
        with pytest.raises(ValueError):
            store.load(refs,codes=['000001.SZ','600000.SH'],start='2026-09-28',end='2026-09-30')


def test_legacy_verifier_report_is_not_mistaken_for_source_rows(tmp_path,monkeypatch):
    import pandas as pd
    from app.market_data.forward_snapshot import digest
    module=importlib.import_module('app.preparation.evidence')
    legacy=tmp_path/'legacy'; legacy.mkdir()
    pd.DataFrame([dict(ts_code='000001.SZ',trade_date='2026-09-28')]).to_parquet(legacy/'status.parquet')
    pd.DataFrame([dict(ts_code='000001.SZ',trade_date='2026-09-28',adj_factor=1.0)]).to_parquet(legacy/'factors.parquet')
    pd.DataFrame([dict(ts_code='000001.SZ',trade_date='2026-09-28',suspend_type=t,suspend_timing=None) for t in ('S','R')]).to_parquet(legacy/'suspensions.parquet')
    hashes={p.name:digest(p) for p in legacy.iterdir()}
    monkeypatch.setattr(module,'verify_snapshot',lambda path:dict(manifest=dict(files=hashes),
        report=dict(start='2026-09-28',end='2026-09-30',source_issue_counts={'SOURCE_CONFLICT':1})))
    with database() as factory:
        store=cls()(tmp_path/'shared',load('source_evidence_repository','SourceEvidenceRepository')(factory))
        result=store.import_legacy_b1(legacy)
        data=store.load(result['references'],codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')
        assert result['source_issue_counts']['SOURCE_CONFLICT']==1
        assert {r['suspend_type'] for r in data['suspend_d']}=={'S','R'}
        assert {p.name:digest(p) for p in legacy.iterdir()}==hashes


def test_verified_empty_single_day_suspension_avoids_repeat_query(tmp_path):
    from preparation_market_support import fixture,build,plan
    with database() as factory:
        store=cls()(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
        refs=store.register_verified(dict(kind='suspend_d',provider='tushare.suspend_d',
            start_date='2026-09-28',end_date='2026-09-28',expected_codes=['000001.SZ'],rows=[]))
        data=store.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')
        assert data['queried_suspensions']==[('000001.SZ','2026-09-28')]
        pool,window,tables,evidence=fixture(); tables['stock_daily'].pop(0)
        evidence.update(data)
        planned=plan(build(pool,window,tables,evidence))
        assert any(r['method']=='daily' for r in planned)
        assert not any(r['method']=='suspend_d' and r['start']=='2026-09-28' for r in planned)


def test_aggregated_suspension_range_does_not_invent_completed_queries(tmp_path):
    with database() as factory:
        store=cls()(tmp_path,load('source_evidence_repository','SourceEvidenceRepository')(factory))
        refs=store.register_verified(dict(kind='suspend_d',provider='tushare.suspend_d',
            start_date='2026-09-28',end_date='2026-09-30',expected_codes=['000001.SZ'],rows=[]))
        data=store.load(refs,codes=['000001.SZ'],start='2026-09-28',end='2026-09-30')
        assert data['queried_suspensions']==[]
