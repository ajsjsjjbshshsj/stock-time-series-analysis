from pathlib import Path
import json
import os
import subprocess
import sys
import pandas as pd
import pytest
from tests.test_csi300_universe import source, NOW
from tests.test_csi300_readiness import data, diagnose
from tests.test_csi300_universe_runner import freeze


def test_offline_runner_import_never_loads_database_configuration(tmp_path):
    app = Path(__file__).resolve().parents[1]
    code = '''import sys, importlib.abc
sys.path.insert(0, sys.argv[1])
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self, fullname, path=None, target=None):
  if fullname.startswith('database') or fullname.startswith('config.logging_config'):
   raise RuntimeError('Offline imported database/logging')
sys.meta_path.insert(0, Block())
import scripts.run_csi300_universe
print('OFFLINE_IMPORT_OK')
'''
    env = {k:v for k,v in os.environ.items() if not k.startswith('DB_')}
    result = subprocess.run([sys.executable,'-c',code,str(app)],cwd=tmp_path,env=env,capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    assert 'OFFLINE_IMPORT_OK' in result.stdout


def test_pool_verifier_failure_leaves_no_completion_marker(tmp_path, monkeypatch):
    from analysis import csi300_universe as u
    origin,_ = source(tmp_path)
    acquired = u.read_acquisition(origin)
    manifest = u.build_universe(acquired,acquired['rows'],NOW)
    out = tmp_path/'csi300_fixed'/manifest['pool_version']
    def reject(*args,**kwargs): raise ValueError('injected verification rejection')
    monkeypatch.setattr(u,'verify_universe',reject)
    with pytest.raises(ValueError): u.publish_universe(origin,out,NOW)
    assert not (out/'complete.json').exists()
    assert (out/'manifest.json').exists()  # failed evidence stays


def test_readiness_verifier_failure_leaves_no_completion_marker(tmp_path,monkeypatch):
    m,pool,_ = freeze(tmp_path,monkeypatch)
    out = m.APP/'models/universes/csi300_readiness/rejected'
    def reject(*args,**kwargs): raise ValueError('injected verifier failure')
    monkeypatch.setattr(m,'verify_readiness',reject)
    with pytest.raises(ValueError): m.run_diagnose(pool,None,'000001.SZ',out,clock=lambda:NOW)
    assert not (out/'complete.json').exists()


def test_invalid_prices_and_missing_sessions_have_no_valid_mature_samples():
    pool,frame,basic,calendar = data(150)
    frame.loc[frame.ts_code=='000001.SZ','close'] = float('nan')
    report = diagnose(pool,frame,basic,calendar)
    assert report['strategies']['single_xgb']['stocks']['000001.SZ']['mature_samples'] == 0
    pool,frame,basic,calendar = data(150)
    frame = frame[~((frame.ts_code=='000001.SZ') & (frame.trade_date==pd.Timestamp('2024-03-01')))]
    assert diagnose(pool,frame,basic,calendar)['strategies']['single_xgb']['stocks']['000001.SZ']['mature_samples'] == 0


def test_frozen_factor_provenance_must_match_original_manifest(tmp_path,monkeypatch):
    m,pool,actual = freeze(tmp_path,monkeypatch)
    _,frame,basic,calendar = data(20)
    factors = frame[['ts_code','trade_date']].assign(adj_factor=1.)
    original = tmp_path/'original.parquet'
    factors.to_parquet(original,index=False)
    proof = tmp_path/'factor_manifest.json'
    from analysis.csi300_universe import digest
    proof.write_text(json.dumps(dict(source='tushare.adj_factor',complete=True,data_path=str(original),
        data_sha256=digest(original),codes=actual['codes'],start=calendar['start'],end=calendar['end'])))
    from analysis.csi300_readiness import frame_identity
    identity = dict(source_mode='strict_database',factor_verified=True,daily_sha256=frame_identity(frame),
        basic_sha256=frame_identity(basic),factors_sha256=frame_identity(factors),
        factor_evidence=dict(data_path=str(original),manifest_path=str(proof),
                              data_sha256=digest(original),manifest_sha256=digest(proof)))
    def injected(*args,**kwargs):
        # Simulate manifest changing after loading, before copying into the output.
        body=json.loads(proof.read_text()); body.update(codes=[],start='2030-01-01',end='2000-01-01')
        proof.write_text(json.dumps(body))
        return frame,basic,factors,identity
    out=m.APP/'models/universes/csi300_readiness/factor_bad'
    now=pd.Timestamp(calendar['end']).to_pydatetime().replace(hour=17,tzinfo=NOW.tzinfo)
    with pytest.raises(ValueError):
        m.run_diagnose(pool,calendar,'000001.SZ',out,clock=lambda:now,read_sources=injected)
    assert not (out/'complete.json').exists()
