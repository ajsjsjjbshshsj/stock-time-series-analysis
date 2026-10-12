from copy import deepcopy
from datetime import datetime,time
import importlib
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from nightly_support import calendar

def module(): return importlib.import_module('app.nightly.contracts')

@pytest.mark.parametrize('now,expected',[
    ('2026-09-29T11:59:59Z',['2026-09-25','2026-09-28']),
    ('2026-09-29T12:00:00Z',['2026-09-25','2026-09-28','2026-09-29']),
    ('2026-09-27T12:00:00Z',['2026-09-25']),
    ('2026-09-28T07:00:00-05:00',['2026-09-25','2026-09-28'])])
def test_due_boundary_and_weekends_use_real_calendar_and_shanghai(now,expected):
    assert module().due_days(calendar(),datetime.fromisoformat(now),'2026-09-25')==expected

def test_custom_time_changes_boundary_without_host_timezone_guess():
    assert module().due_days(calendar(),datetime.fromisoformat('2026-09-29T12:30:00Z'),'2026-09-28','21:00')==['2026-09-28']
    assert module().parse_run_at('20:00')==time(20)

@pytest.mark.parametrize('bad',['16:00','00:00','25:00','20:60','8:00','２０:００',None,20])
def test_invalid_or_preclose_run_time_rejected(bad):
    with pytest.raises(ValueError): module().parse_run_at(bad)

@pytest.mark.parametrize('broken',['gap','duplicate','bool','wrong_source'])
def test_calendar_uncertainty_is_not_guessed(broken):
    value=deepcopy(calendar())
    if broken=='gap':value['rows'].pop(1)
    elif broken=='duplicate':value['rows'].append(value['rows'][0])
    elif broken=='bool':value['rows'][0]['is_open']=True
    else:value['source']='weekday_guess'
    with pytest.raises(ValueError):module().due_days(value,datetime.fromisoformat('2026-09-29T12:00:00Z'),'2026-09-25')

def test_naive_clock_and_before_retention_start_rejected():
    with pytest.raises(ValueError):module().due_days(calendar(),datetime(2026,9,29,20),'2026-09-25')
    with pytest.raises(ValueError):module().due_days(calendar(),datetime.fromisoformat('2026-09-29T12:00:00Z'),'2022-12-31')

def test_future_scope_has_no_fake_due_day():
    assert module().due_days(calendar(),datetime.fromisoformat('2026-09-29T11:00:00Z'),'2026-09-29')==[]
