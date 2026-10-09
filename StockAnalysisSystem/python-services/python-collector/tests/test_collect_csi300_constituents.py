import importlib
import pytest
from tests.test_csi300_constituent_snapshot import Client, EPOCH


def main(*args, **kwargs):
    try: module = importlib.import_module('scripts.collect_csi300_constituents')
    except ModuleNotFoundError: pytest.fail('CSI300 collector CLI missing')
    return module.main(*args, **kwargs)


def test_cli_one_shot_acquires_without_database(tmp_path, capsys):
    args = ['--as-of', '2026-10-09', '--output', str(tmp_path/'source'),
            '--request-state', str(tmp_path/'state.json')]
    assert main(args, client=Client(), clock=lambda: EPOCH, sleep=lambda s: None) == 0
    assert 'COMPLETE' in capsys.readouterr().out


def test_provider_exception_does_not_leak_secret(tmp_path, capsys):
    class Forbidden:
        def index_weight(self, **kw): raise RuntimeError('权限 denied sensitive-token-12345678901234567890')
    args = ['--as-of', '2026-10-09', '--output', str(tmp_path/'source'),
            '--request-state', str(tmp_path/'state.json')]
    assert main(args, client=Forbidden(), clock=lambda: EPOCH, sleep=lambda s: None) == 1
    output = capsys.readouterr()
    assert 'sensitive-token' not in output.err+output.out
    assert 'sensitive-token' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))
