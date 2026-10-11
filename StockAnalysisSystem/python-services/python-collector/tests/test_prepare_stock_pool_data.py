from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import importlib.util
import json
from types import SimpleNamespace

import pytest
from preparation_store_support import database
from test_preparation_service import make


def cli():
    path=Path(__file__).resolve().parents[1]/'scripts/prepare_stock_pool_data.py'
    if not path.is_file(): pytest.fail('unified preparation CLI not implemented')
    spec=importlib.util.spec_from_file_location('unified_preparation_cli',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_status_uses_no_sdk_and_does_not_expose_owner_token(capsys):
    tasks=SimpleNamespace(get=lambda task_id:dict(task_id=task_id,status='RUNNING',owner_token='SECRET_OWNER',owner={'token':'SECRET_OWNER'}))
    assert cli().main(['status','--task-id','a'*32],dependencies={'tasks':tasks})==0
    out=capsys.readouterr().out
    assert 'SECRET_OWNER' not in out
    assert json.loads(out)['status']=='RUNNING'


def test_submit_only_queues_and_does_not_collect(tmp_path,capsys):
    module=cli()
    with database() as factory:
        service,pools,tasks,market,acquirer,req=make(factory,tmp_path)
        result=module.main(['submit','--pool-version',req['pool_version'],'--start',req['requested_start'],'--end',req['requested_end']],
                          dependencies={'service':service})
        assert result==0
        assert json.loads(capsys.readouterr().out)['status']=='QUEUED'
        assert market.writes==0 and acquirer.calls==[]


def test_inspect_conflict_is_report_not_fake_ready(tmp_path,capsys):
    module=cli()
    with database() as factory:
        service,_,_,_,_,req=make(factory,tmp_path,conflict=True)
        result=module.main(['inspect','--pool-version',req['pool_version'],'--start',req['requested_start'],'--end',req['requested_end']],dependencies={'service':service})
        assert result==0
        assert json.loads(capsys.readouterr().out)['data_ready'] is False


def test_raw_dependency_error_is_hidden(capsys):
    module=cli()
    def broken(task_id): raise RuntimeError('private upstream body SECRET_TOKEN')
    result=module.main(['status','--task-id','a'*32],dependencies={'tasks':SimpleNamespace(get=broken)})
    assert result!=0
    captured=capsys.readouterr()
    assert 'SECRET_TOKEN' not in captured.out+captured.err


def test_worker_once_reports_partial_as_nonzero(capsys):
    module=cli()
    worker=SimpleNamespace(run_once=lambda:dict(status='PARTIAL',result={'data_ready':False}))
    assert module.main(['worker','--once'],dependencies={'worker':lambda:worker})==1
    assert json.loads(capsys.readouterr().out)['status']=='PARTIAL'


def test_existing_active_pool_create_does_not_fetch_unless_explicit_refresh(capsys):
    module=cli(); sources=[]
    pools=SimpleNamespace(resolve=lambda **kwargs:dict(pool_version='fixed-existing',pool_id='index:000300.SH'))
    def forbidden(): sources.append(1); pytest.fail('existing fixed pool must not silently refetch members')
    result=module.main(['pool-create','--type','index','--code','000300.SH','--as-of','2026-10-11'],
        dependencies={'pools':pools,'pool_sources':forbidden})
    assert result==0 and sources==[]
    assert json.loads(capsys.readouterr().out)['pool_version']=='fixed-existing'


def test_cli_does_not_accept_arbitrary_output_path():
    with pytest.raises(SystemExit) as caught:
        cli().main(['status','--task-id','a'*32,'--output','C:/Windows'],dependencies={})
    assert caught.value.code==2
