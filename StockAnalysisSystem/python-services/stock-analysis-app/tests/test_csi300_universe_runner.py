from datetime import datetime, timezone, timedelta
import importlib
import shutil
import json
from pathlib import Path
import pandas as pd
import pytest
from tests.test_csi300_universe import source, NOW
from tests.test_csi300_readiness import data


def module():
    try: return importlib.import_module('scripts.run_csi300_universe')
    except ModuleNotFoundError: pytest.fail('CSI300 freeze/readiness runner missing')


def freeze(tmp_path, monkeypatch):
    m = module()
    app = tmp_path/'app'
    monkeypatch.setattr(m, 'APP', app)
    root = app/'models/universes/csi300_fixed'
    def collect(output, requested):
        fixture = tmp_path/'fixture'
        fixture.mkdir()
        origin, _ = source(fixture)
        shutil.copytree(origin, output)
    result = m.run_freeze(root, '2026-10-09', clock=lambda: NOW, collect=collect)
    return m, root/result['pool_version'], result


def test_completed_fixed_pool_stays_fixed_on_later_clock(tmp_path, monkeypatch):
    m, pool, result = freeze(tmp_path, monkeypatch)
    before = {str(p):p.read_bytes() for p in pool.parent.rglob('*') if p.is_file()}
    def no_calls(*args): pytest.fail('fixed pool repeat must not call SDK')
    again = m.run_freeze(pool.parent, None, clock=lambda:NOW+timedelta(days=1), collect=no_calls)
    assert again == result
    assert before == {str(p):p.read_bytes() for p in pool.parent.rglob('*') if p.is_file()}


def test_paths_and_future_date_reject_before_collection(tmp_path, monkeypatch):
    m = module()
    monkeypatch.setattr(m, 'APP', tmp_path/'app')
    def no_calls(*args): pytest.fail('invalid request must not collect')
    with pytest.raises(ValueError): m.run_freeze(tmp_path/'wrong', '2026-10-09', clock=lambda:NOW, collect=no_calls)
    with pytest.raises(ValueError): m.run_freeze(m.APP/'models/universes/csi300_fixed', '2026-10-10', clock=lambda:NOW, collect=no_calls)


def test_diagnostic_is_offline_reproducible_and_model_not_ready(tmp_path, monkeypatch):
    m, pool, _ = freeze(tmp_path, monkeypatch)
    _, frame, basic, calendar = data(150)
    now = pd.Timestamp(calendar['end']).to_pydatetime().replace(hour=17, tzinfo=NOW.tzinfo)
    # data fixture dates precede membership freeze: fixed-current-pool historical diagnostic, not blind testing.
    out = m.APP/'models/universes/csi300_readiness/run_test'
    from analysis.csi300_readiness import frame_identity
    def read_sources(universe, start, end):
        return frame, basic, None, dict(source_mode='strict_database', factor_verified=False,
             daily_sha256=frame_identity(frame), basic_sha256=frame_identity(basic), factors_sha256=None)
    report = m.run_diagnose(pool, calendar, '000001.SZ', out, clock=lambda:now, read_sources=read_sources)
    assert len(report['stocks']) == 300
    before = {str(p):p.read_bytes() for p in out.rglob('*') if p.is_file()}
    assert m.verify_readiness(out) == report
    assert before == {str(p):p.read_bytes() for p in out.rglob('*') if p.is_file()}
    assert not report['strategies']['transformer']['data_ready']
    assert report['strategies']['single_xgb']['data_ready']


def test_no_calendar_is_not_ready_without_database_access(tmp_path, monkeypatch):
    m, pool, _ = freeze(tmp_path, monkeypatch)
    def no_db(*args): pytest.fail('missing calendar must not read DB')
    out = m.APP/'models/universes/csi300_readiness/no_calendar'
    report = m.run_diagnose(pool, None, '000001.SZ', out, clock=lambda:NOW, read_sources=no_db)
    assert report['status'] == 'NOT_READY'
    assert report['target_trade_date'] is None
    assert m.verify_readiness(out) == report


def test_report_tamper_is_not_accepted_as_complete(tmp_path, monkeypatch):
    m, pool, _ = freeze(tmp_path, monkeypatch)
    out = m.APP/'models/universes/csi300_readiness/no_calendar'
    m.run_diagnose(pool, None, '000001.SZ', out, clock=lambda:NOW, read_sources=lambda *a:None)
    body = json.loads((out/'readiness.json').read_text(encoding='utf-8'))
    body['status'] = 'DATA_READY'
    (out/'readiness.json').write_text(json.dumps(body))
    with pytest.raises(ValueError): m.verify_readiness(out)


def test_unknown_stock_rejected_before_source_read(tmp_path, monkeypatch):
    m, pool, _ = freeze(tmp_path, monkeypatch)
    def no_db(*args): pytest.fail('unknown stock must not read DB')
    with pytest.raises(ValueError):
        m.run_diagnose(pool, None, '600000.SH', m.APP/'models/universes/csi300_readiness/bad',
                       clock=lambda:NOW, read_sources=no_db)
