from datetime import datetime, timezone, timedelta
import copy
import importlib

import pandas as pd
import pytest

TZ = timezone(timedelta(hours=8))


def calendar():
    return dict(source='tushare.trade_cal', exchange='SSE', start='2026-09-30', end='2026-10-12',
                rows=[dict(cal_date=str(day.date()), is_open=int(str(day.date()) in
                      {'2026-09-30', '2026-10-08', '2026-10-09', '2026-10-12'}))
                      for day in pd.date_range('2026-09-30', '2026-10-12')])


def daily():
    return pd.DataFrame([dict(ts_code=code, trade_date=day, open=10., high=11., low=9.,
                             close=10., vol=100., amount=1000.)
                         for code in ['000001.SZ', '600000.SH']
                         for day in ['2026-09-30', '2026-10-08', '2026-10-09']])


def select(now, frame=None, cal=None, **kwargs):
    try:
        module = importlib.import_module('analysis.strategy_dates')
    except ModuleNotFoundError:
        pytest.fail('strategy cutoff contract is not implemented')
    return module.select_data_cutoff(cal or calendar(), now, daily() if frame is None else frame,
                                    ['000001.SZ', '600000.SH'], **kwargs)


@pytest.mark.parametrize('stamp,target', [
    ('2026-10-09T15:59:59+08:00', '2026-10-08'),
    ('2026-10-09T16:00:00+08:00', '2026-10-08'),
    ('2026-10-09T16:00:01+08:00', '2026-10-09'),
    ('2026-10-09T08:00:01+00:00', '2026-10-09'),
    ('2026-10-10T17:00:00+08:00', '2026-10-09'),
    ('2026-10-06T17:00:00+08:00', '2026-09-30'),
])
def test_cutoff_uses_strict_shanghai_time_and_actual_sessions(stamp, target):
    result = select(datetime.fromisoformat(stamp))
    assert result['target_trade_date'] == result['data_cutoff'] == target
    assert result['fallback_reason'] is None
    assert result['request_time'].endswith('+08:00')


@pytest.mark.parametrize('column,value', [('close', None), ('vol', -1), ('open', 0)])
def test_incomplete_target_falls_back_without_mutation(column, value):
    frame = daily()
    frame.loc[frame.trade_date == '2026-10-09', column] = value
    before = frame.copy(deep=True)
    result = select(datetime(2026, 10, 9, 17, tzinfo=TZ), frame)
    assert result['target_trade_date'] == '2026-10-09'
    assert result['data_cutoff'] == '2026-10-08'
    assert result['fallback_reason'] == 'INCOMPLETE_DAILY'
    pd.testing.assert_frame_equal(frame, before)


def test_missing_stock_and_factor_do_not_shrink_pool():
    frame = daily()
    factors = frame[['ts_code', 'trade_date']].assign(adj_factor=1.)
    factors = factors[~((factors.ts_code == '000001.SZ') & (factors.trade_date == '2026-10-09'))]
    result = select(datetime(2026, 10, 9, 17, tzinfo=TZ), factors=factors)
    assert result['data_cutoff'] == '2026-10-08'
    assert result['fallback_reason'] == 'INCOMPLETE_FACTORS'
    frame = frame[~((frame.ts_code == '000001.SZ') & (frame.trade_date == '2026-10-09'))]
    assert select(datetime(2026, 10, 9, 17, tzinfo=TZ), frame)['data_cutoff'] == '2026-10-08'


@pytest.mark.parametrize('kind', ['naive', 'calendar_gap', 'out_of_coverage', 'duplicate', 'empty', 'missing_column'])
def test_invalid_or_unusable_input_is_not_reported_as_latest(kind):
    now = datetime(2026, 10, 9, 17, tzinfo=TZ)
    frame, cal = daily(), copy.deepcopy(calendar())
    if kind == 'naive': now = now.replace(tzinfo=None)
    if kind == 'calendar_gap': cal['rows'].pop(3)
    if kind == 'out_of_coverage': now = datetime(2026, 10, 13, 17, tzinfo=TZ)
    if kind == 'duplicate': frame = pd.concat([frame, frame.iloc[:1]])
    if kind == 'empty': frame = frame.iloc[:0]
    if kind == 'missing_column': frame = frame.drop(columns='close')
    with pytest.raises(ValueError):
        select(now, frame, cal)
