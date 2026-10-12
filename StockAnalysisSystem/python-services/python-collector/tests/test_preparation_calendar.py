"""Trading calendar is authoritative; 16:00 itself is not a completed session."""
import importlib
from datetime import datetime, timedelta, date

import pytest


def api():
    try:
        return importlib.import_module('app.preparation.calendar')
    except ModuleNotFoundError:
        pytest.fail('preparation calendar not implemented')


def calendar():
    rows = []
    start = date(2026, 9, 28)
    for i in range(16):
        day = start + timedelta(days=i)
        rows.append(dict(cal_date=day.isoformat(), is_open=int(day.isoformat() in
            {'2026-09-28', '2026-09-29', '2026-09-30', '2026-10-09', '2026-10-12'})))
    return dict(source='tushare.trade_cal', rows=rows)


@pytest.mark.parametrize('now,end', [
    ('2026-10-09T16:00:00+08:00', '2026-09-30'),
    ('2026-10-09T16:00:01+08:00', '2026-10-09'),
    ('2026-10-11T17:00:00+08:00', '2026-10-09'),
    ('2026-10-03T17:00:00+08:00', '2026-09-30'),
    ('2026-10-09T08:00:01+00:00', '2026-10-09')])
def test_cutoff_uses_shanghai_time_and_real_calendar(now, end):
    result = api().resolve_window('2026-09-28', '2026-10-13', calendar(), datetime.fromisoformat(now))
    assert result['end'] == end
    assert result['requested_end'] == '2026-10-13'
    assert result['cutoff_reason'] == 'CLAMPED_TO_COMPLETED_SESSION'


def test_requested_historical_end_not_extended():
    result = api().resolve_window('2026-09-28', '2026-09-29', calendar(),
                                 datetime.fromisoformat('2026-10-11T17:00:00+08:00'))
    assert result['end'] == '2026-09-29'
    assert result['cutoff_reason'] is None


def test_future_only_range_explicitly_empty():
    result = api().resolve_window('2026-10-12', '2026-10-13', calendar(),
                                 datetime.fromisoformat('2026-10-11T17:00:00+08:00'))
    assert result['empty'] is True
    assert result['cutoff_reason'] == 'EMPTY_EFFECTIVE_RANGE'


@pytest.mark.parametrize('failure', ['gap', 'duplicate', 'invalid', 'source'])
def test_calendar_cannot_silently_fall_back_to_weekdays(failure):
    data = calendar()
    if failure == 'gap':
        data['rows'].pop(4)
    elif failure == 'duplicate':
        data['rows'].append(data['rows'][0])
    elif failure == 'invalid':
        data['rows'][0]['is_open'] = True
    else:
        data['source'] = 'weekends'
    with pytest.raises(ValueError):
        api().resolve_window('2026-09-28', '2026-10-13', data,
                             datetime.fromisoformat('2026-10-11T17:00:00+08:00'))


def test_naive_clock_rejected():
    with pytest.raises(ValueError):
        api().resolve_window('2026-09-28', '2026-10-13', calendar(), datetime(2026, 10, 11, 17))


def test_historical_request_reuses_calendar_without_fetching_later_dates():
    data = calendar()
    data['rows'] = data['rows'][:3]
    result = api().resolve_window('2026-09-28', '2026-09-30', data,
                                 datetime.fromisoformat('2026-10-11T17:00:00+08:00'))
    assert result['end'] == '2026-09-30'
    assert result['empty'] is False

