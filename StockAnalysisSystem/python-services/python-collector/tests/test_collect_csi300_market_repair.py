"""CLI stage boundaries: no credential access or insertion without consent."""
import importlib.util
from pathlib import Path
import pandas as pd
import pytest

from tests.test_market_repair_contract import portable_pool, inputs
from tests.test_market_repair_acquisition import Clock


def module():
    file=Path(__file__).resolve().parents[1]/'scripts/collect_csi300_market_repair.py'
    assert file.exists(),'repair CLI is not implemented'
    spec=importlib.util.spec_from_file_location('_repair_cli_test',file)
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def test_import_cli_without_allow_insert_never_reads_database(tmp_path):
    m=module()
    assert m.main(['--stage','import','--output',str(tmp_path)])==2


def test_invalid_snapshot_is_rejected_before_repository_factory(tmp_path):
    m=module()
    from tests import test_market_repair_contract as fixture
    def forbidden(): pytest.fail('Repository initialized before immutable source verification')
    with pytest.raises(ValueError):
        m.run_stage('import',Path(__file__).resolve().parents[4],fixture.POOL,tmp_path,'2023-10-09','2023-10-11',{'repo_factory':forbidden})


def test_inventory_acquire_assemble_verify_resume_without_credentials(tmp_path,monkeypatch):
    m=module(); from tests import test_market_repair_contract as fixture
    cal,tables=inputs(); clock=Clock(); calls=[]
    class Repo:
        def capture_baseline(self,*args): return tables
    class Empty:
        def __getattr__(self,method):
            def call(**params):
                calls.append(method)
                return pd.DataFrame(columns=params['fields'].split(','))
            return call
    deps=dict(repo_factory=Repo,calendar_factory=lambda:cal,client_factory=lambda:Empty(),
        request_state=tmp_path/'requests.json',clock=clock,sleep=clock.sleep)
    root=Path(__file__).resolve().parents[4]; output=tmp_path/'repair'
    def run(stage): return m.run_stage(stage,root,fixture.POOL,output,'2023-10-09','2023-10-11',deps)
    run('inventory'); assert run('acquire')['acquisition_complete']
    with pytest.raises(ValueError): m.run_stage('inventory',root,fixture.POOL,output,'2023-10-10','2023-10-11',deps)
    report=run('assemble'); assert report['model_ready'] is False
    deps['client_factory']=lambda:pytest.fail('Completed source initialized SDK/Token')
    deps['repo_factory']=lambda:pytest.fail('Offline verification initialized database')
    monkeypatch.delenv('TUSHARE_TOKEN',raising=False); monkeypatch.delenv('DB_PASSWORD',raising=False)
    n=len(calls); assert run('acquire')['acquisition_complete']; assert len(calls)==n
    assert run('verify')['model_ready'] is False
    (output/'snapshot/report.json').write_text('{}',encoding='utf-8')
    with pytest.raises(ValueError): run('verify')


def test_invalid_stage_and_outside_window_are_rejected(tmp_path):
    m=module(); from tests import test_market_repair_contract as fixture
    root=Path(__file__).resolve().parents[4]
    with pytest.raises(ValueError): m.run_stage('train',root,fixture.POOL,tmp_path,'2023-10-09','2023-10-11',{})
    with pytest.raises(ValueError): m.run_stage('inventory',root,fixture.POOL,tmp_path,'2023-10-09','2026-10-09',{})
