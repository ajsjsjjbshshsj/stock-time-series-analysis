"""Single-shot CLI boundary without external service credentials or database."""
import importlib
import json
import pandas as pd


def test_calendar_cli_uses_injected_provider_and_writes_auditable_snapshot(tmp_path):
    module=importlib.import_module('scripts.collect_forward_snapshot')
    contract=tmp_path/'request.json'
    contract.write_text(json.dumps({'start':'2026-10-08','end':'2026-10-08'}),encoding='utf-8')
    class Client:
        def trade_calendar(self,**kwargs):
            assert kwargs['exchange']=='SZSE'
            return pd.DataFrame({'exchange':['SZSE'],'cal_date':['20261008'],'is_open':[1]})
    code=module.main(['--stage','calendar','--contract',str(contract),'--output',str(tmp_path/'out')],client=Client())
    assert code==0 and (tmp_path/'out/calendar_manifest.json').is_file()


def test_cli_hides_secret_exception_without_traceback(tmp_path,capsys):
    module=importlib.import_module('scripts.collect_forward_snapshot')
    contract=tmp_path/'request.json'
    contract.write_text(json.dumps({'start':'2026-10-08','end':'2026-10-08'}),encoding='utf-8')
    class Client:
        def trade_calendar(self,**kwargs):
            raise RuntimeError('invalid token secret-should-never-print')
    assert module.main(['--stage','calendar','--contract',str(contract),'--output',str(tmp_path/'out')],client=Client())==1
    captured=capsys.readouterr()
    assert 'secret-should-never-print' not in captured.out+captured.err
