import sys
from types import SimpleNamespace
import pytest


def test_import_help_no_connector(monkeypatch, capsys):
    from database import db_connector
    monkeypatch.setattr(db_connector, 'DatabaseConnector', lambda *a: pytest.fail('unexpected connection'))
    from scripts.evaluate import main
    with pytest.raises(SystemExit) as exit: main(['--help'])
    assert exit.value.code == 0
    assert '--calendar' in capsys.readouterr().out


@pytest.mark.parametrize('stock,start,end,calendar', [
    ('600000.SH','2020-01-01','2024-01-01','missing'),
    ('000001.SZ','bad','2024-01-01','missing'),
    ('000001.SZ','2024-01-01','2020-01-01','missing'),
    ('000001.SZ','2020-01-01','2024-01-01','missing')])
def test_invalid_arguments_fail_without_configuration(stock,start,end,calendar,monkeypatch,capsys):
    from scripts.evaluate import main
    monkeypatch.setitem(sys.modules, 'config.settings', None)
    assert main(['--stock',stock,'--start',start,'--end',end,'--calendar',calendar]) == 1
    assert capsys.readouterr().err == 'Evaluation failed: check inputs, history, reports and database availability.\n'


def test_cli_fixed_pipeline_and_sanitized_failure(monkeypatch,capsys):
    from scripts import evaluate as cli
    calls = []
    class Connector:
        def __init__(self, config):
            assert all(config[k] == 5 for k in ('connect_timeout','pool_timeout','read_timeout','write_timeout'))
        def __enter__(self): return self
        def __exit__(self,*args): pass
    from database import db_connector
    monkeypatch.setattr(db_connector,'DatabaseConnector',Connector)
    monkeypatch.setitem(sys.modules,'config.settings',SimpleNamespace(DATABASE_CONFIG={}))
    monkeypatch.setattr(cli,'load_calendar',lambda p: {'calendar': True})
    monkeypatch.setattr(cli,'validate_calendar',lambda c,s: calls.append('calendar'))
    monkeypatch.setattr(cli.ForecastRepository,'load_market',lambda self,*args: calls.append('select') or 'frame')
    monkeypatch.setattr(cli,'evaluate',lambda *args: calls.append('evaluate') or dict(report_id='a'*32,ts_code='000001.SZ'))
    monkeypatch.setattr(cli.EvaluationRepository,'publish',lambda self,r: calls.append('publish') or r['report_id'])
    args=['--stock','000001.SZ','--start','2020-01-01','--end','2024-01-01','--calendar','calendar.json']
    assert cli.main(args) == 0
    assert calls == ['calendar','select','evaluate','publish']
    assert capsys.readouterr().out == 'Published '+'a'*32+' for 000001.SZ\n'
    monkeypatch.setattr(cli.EvaluationRepository,'publish',lambda *a: (_ for _ in ()).throw(RuntimeError('secret password SQL')))
    assert cli.main(args) == 1
    output = capsys.readouterr()
    assert output.out == '' and 'secret' not in output.err
