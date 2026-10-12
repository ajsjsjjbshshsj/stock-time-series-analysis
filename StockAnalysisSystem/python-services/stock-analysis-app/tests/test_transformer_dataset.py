"""Windows follow observed trading sessions, never consecutive calendar days."""
import numpy as np
import pandas as pd
import pytest
from analysis.transformer_utils import create_ranking_dataset_vectorized


def data():
    dates = pd.bdate_range('2024-01-02', periods=100)
    return pd.concat([pd.DataFrame(dict(instrument=i, 日期=dates,
        f=np.arange(100, dtype=float), label=np.ones(100) * (i + 1) / 100))
        for i in range(10)], ignore_index=True)


def test_weekends_and_mature_tail_do_not_discard_windows():
    result = create_ranking_dataset_vectorized(data(), ['f'], 60)
    assert len(result) == 6
    assert len(result[0]) == 41
    np.testing.assert_array_equal([seq[0, -1, 0] for seq in result[0]], np.arange(59, 100))
    assert all(seq.shape == (10, 60, 1) for seq in result[0])


def test_unlabeled_history_is_context_but_not_a_target():
    frame = data()
    frame.loc[frame.f.isin([30, 70, 99]), 'label'] = np.nan
    result = create_ranking_dataset_vectorized(frame, ['f'], 60)
    ends = [seq[0, -1, 0] for seq in result[0]]
    assert ends == [i for i in range(59, 100) if i not in [70, 99]]
    np.testing.assert_array_equal(result[0][0][0, :, 0], np.arange(60))
    assert all(np.isfinite(target).all() for target in result[1])


def test_validation_boundary_only_filters_window_end():
    frame = data()
    boundary = frame['日期'].unique()[65]
    result = create_ranking_dataset_vectorized(frame, ['f'], 60, min_window_end_date=boundary)
    assert len(result[0]) == 35
    np.testing.assert_array_equal(result[0][0][0, :, 0], np.arange(6, 66))


@pytest.mark.parametrize('length', [0, -1, 1.5, True])
def test_invalid_window_length_is_explicit(length):
    with pytest.raises(ValueError, match='sequence|length'):
        create_ranking_dataset_vectorized(data(), ['f'], length)


def test_duplicate_session_is_rejected_instead_of_duplicating_context():
    frame = data()
    with pytest.raises(ValueError, match='Duplicate|duplicate'):
        create_ranking_dataset_vectorized(pd.concat([frame, frame.iloc[:1]]), ['f'], 60)
