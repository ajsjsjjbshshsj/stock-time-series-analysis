"""The existing CLI owns backfill, nightly policy and safe public output."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import sys, json
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from test_prepare_stock_pool_data import cli


def test_backfill_dry_run_no_sdk_no_write(capsys):
    calls = []
    def prepare(start, end, **kwargs):
        calls.append((start, end, kwargs['acquire_reference'], kwargs.get('persist')))
        return dict(status='DRY_RUN', strategy='market-gap-v1', api_requests=0, planned_missing_keys=5)
    deps = dict(market_service=lambda: SimpleNamespace(prepare_batch=prepare))
    assert cli().main(['market-backfill', '--start', '2023-01-01', '--end', '2026-10-11', '--dry-run'], dependencies=deps) == 0
    assert calls == [('2023-01-01', '2026-10-11', False, False)]
    assert json.loads(capsys.readouterr().out)['api_requests'] == 0


def test_nightly_default_gap_policy_and_20_clock(monkeypatch):
    from app.repositories.gap_task_repository import GapTaskRepository
    module = cli()
    runtime = module.Runtime.__new__(module.Runtime)
    runtime._market_tasks = None
    runtime.db = SimpleNamespace(Session=lambda: None)
    runtime.data_root = Path('gap-test-no-write')
    assert isinstance(runtime.market_tasks(), GapTaskRepository)
    monkeypatch.delenv('NIGHTLY_UPDATE_AT', raising=False)
    received = []
    worker = SimpleNamespace(run_once=lambda: None)
    def run(**kwargs):
        received.append(kwargs)
        return worker
    assert module.main(['worker', '--nightly', '--once'], dependencies=dict(worker=run)) == 0
    assert received == [dict(nightly=True, run_at='20:00', bootstrap_start=None)]


@pytest.mark.parametrize('start,end', [('2022-12-31','2026-10-11'), ('bad','2026-10-11'), ('2026-10-11','2023-01-01')])
def test_invalid_scope_rejected_before_runtime(start, end, monkeypatch):
    module = cli()
    monkeypatch.setattr(module, 'Runtime', lambda: pytest.fail('RUNTIME_BEFORE_VALIDATION'))
    with pytest.raises(SystemExit) as error:
        module.main(['market-backfill', '--start', start, '--end', end, '--dry-run'])
    assert error.value.code == 2


def test_public_output_no_owner_or_token():
    value = cli().public(dict(owner_token='secret', owner={'token':'secret'}, request={'token':'secret'},
        result=dict(model_ready=False, verification_mode='missing-only')))
    assert 'secret' not in json.dumps(value)
    assert value['result']['verification_mode'] == 'missing-only'


def test_no_shutdown_option():
    with pytest.raises(SystemExit) as error:
        cli().main(['market-backfill', '--start', '2023-01-01', '--end', '2026-10-11', '--shutdown'], dependencies={})
    assert error.value.code == 2
