"""Inference windows use historical sessions only, never target maturity."""
import importlib
import numpy as np
import pandas as pd
import pytest


def api():
    return importlib.import_module('analysis.transformer_portfolio_signals')


def context():
    dates = pd.bdate_range('2026-06-01', periods=4)
    frame = pd.DataFrame([{'日期': date, '股票代码': code, 'instrument': index,
                           'x': float(day+index*10), 'label': np.nan}
                          for index, code in enumerate(('A', 'B')) for day, date in enumerate(dates)])
    return dates, frame, {'A': 0, 'B': 1}


def test_unlabeled_last_session_has_complete_historical_window():
    dates, frame, mapping = context()
    outputs = list(api().history_windows(frame, ['x'], mapping, [dates[-1]], 2))
    date, sequence, ids = outputs[0]
    assert date == dates[-1]
    np.testing.assert_array_equal(sequence, [[[2.], [3.]], [[12.], [13.]]])
    assert ids == [0, 1]


def test_future_feature_values_cannot_enter_earlier_window():
    dates, frame, mapping = context()
    frame.loc[frame['日期'] >= dates[2], 'x'] = 100000.
    _, sequence, _ = list(api().history_windows(frame, ['x'], mapping, [dates[1]], 2))[0]
    np.testing.assert_array_equal(sequence, [[[0.], [1.]], [[10.], [11.]]])


@pytest.mark.parametrize('bad', ['duplicate', 'missing_session', 'wrong_mapping', 'nonfinite', 'short'])
def test_incomplete_or_wrong_historical_window_fails_closed(bad):
    dates, frame, mapping = context()
    request = [dates[2]]
    if bad == 'duplicate':
        frame = pd.concat([frame, frame.iloc[[0]]])
    elif bad == 'missing_session':
        frame = frame[~((frame.instrument == 1) & (frame['日期'] == dates[1]))]
    elif bad == 'wrong_mapping':
        mapping = {'A': 1, 'B': 0}
    elif bad == 'nonfinite':
        frame.loc[(frame.instrument == 1) & (frame['日期'] == dates[1]), 'x'] = np.nan
    else:
        request = [dates[0]]
    with pytest.raises(ValueError, match='window|mapping|session|feature'):
        list(api().history_windows(frame, ['x'], mapping, request, 2))
