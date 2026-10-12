import json
import pytest
from tests.test_csi300_constituent_snapshot import module, Client, EPOCH
from scripts import collect_csi300_constituents as cli


def test_migration_uses_same_shared_request_lock(tmp_path):
    state=tmp_path/'request_state.json'
    state.write_text(json.dumps(dict(retry_not_before=10000,last_request_at=1000)))
    state.with_suffix('.lock').write_text('other-worker')
    legacy=tmp_path/'legacy'; legacy.mkdir()
    (legacy/'request_state.json').write_text(json.dumps(dict(retry_not_before=100)))
    before=state.read_bytes()
    try: migrate=module().migrate_request_state
    except AttributeError: pytest.fail('locked canonical quota migration missing')
    with pytest.raises(RuntimeError,match='BUSY'): migrate(state,legacy_root=legacy)
    assert state.read_bytes()==before


def test_direct_collector_cli_inherits_known_legacy_cooldown(tmp_path,monkeypatch):
    project=tmp_path/'project'
    monkeypatch.setattr(module(),'PROJECT',project,raising=False)
    old=project/'python-services/stock-analysis-app/models/transformer/forward_signals_20261008'
    old.mkdir(parents=True)
    (old/'request_state.json').write_text(json.dumps(dict(retry_not_before=EPOCH+100,failure='MINUTE_QUOTA')))
    client=Client()
    state=project/'.runtime/market_requests/request_state.json'
    assert cli.main(['--as-of','2026-10-09','--output',str(tmp_path/'source'),'--request-state',str(state)],
                     client=client,clock=lambda:EPOCH,sleep=lambda s:None)==1
    assert not client.calls
    assert json.loads(state.read_text())['retry_not_before']==EPOCH+100


def test_collector_verification_failure_does_not_publish_complete(tmp_path,monkeypatch):
    m=module()
    def reject(*args,**kwargs): raise ValueError('injected source verifier failure')
    monkeypatch.setattr(m,'verify_constituent_evidence',reject)
    with pytest.raises(ValueError):
        m.collect_constituent_evidence(Client(),'2026-10-09',tmp_path/'source',request_state=tmp_path/'state.json',
                                       clock=lambda:EPOCH,sleep=lambda s:None)
    assert not (tmp_path/'source/complete.json').exists()
