import numpy as np
import pytest


def score(ranking, policy='nonnegative_variance'):
    from analysis.transformer_scoring import adjust_scores
    return adjust_scores(ranking, [0., 1., .5], [1., 0., .5], [0., 1., .5], policy)


def test_negative_scores_are_nonnegative_before_discount():
    adjusted, uncertainty = score([-3., -2., -1.])
    np.testing.assert_allclose(adjusted, [0., 7/18, 1.])
    np.testing.assert_allclose(uncertainty, [2/9, 2/9, 0.])


def test_equal_negative_score_not_promoted_by_disagreement():
    from analysis.transformer_scoring import adjust_scores
    adjusted, _ = adjust_scores([-2., -1., -1.], [0., 1., .5], [0., 0., .5], [0., 1., .5], 'nonnegative_variance')
    assert adjusted[1] <= adjusted[2]


def test_shift_and_positive_scale_invariance():
    expected, _ = score([-3., -2., -1.])
    np.testing.assert_allclose(score([7., 8., 9.])[0], expected)
    np.testing.assert_allclose(score([-6., -4., -2.])[0], expected)


def test_constant_scores_degenerate_to_zero():
    np.testing.assert_array_equal(score([-1., -1., -1.])[0], [0., 0., 0.])


def test_legacy_default_is_unchanged():
    from analysis.transformer_scoring import adjust_scores, score_policy
    adjusted, _ = adjust_scores([2., 4.], [0., 1.], [1., 0.], [0., 1.])
    np.testing.assert_allclose(adjusted, [14/9, 28/9])
    assert score_policy({}) == 'legacy_variance'


@pytest.mark.parametrize('ranking', [[], [np.nan, 1., 2.], [[1., 2., 3.]], [1., 2.]])
def test_invalid_vectors_rejected(ranking):
    with pytest.raises(ValueError, match='finite|shape|vector'):
        score(ranking)


def test_unknown_policy_rejected():
    with pytest.raises(ValueError, match='policy'):
        score([1., 2., 3.], 'unknown')


def test_experiment_uses_explicit_shared_policy():
    import torch
    from analysis.transformer_experiment import adjusted_head_scores
    outputs = {key: torch.tensor([values]) for key, values in {
        'ranking': [-3., -2., -1.], 'regression': [0., 1., .5],
        'classification': [1., 0., .5], 'direction': [0., 1., .5]}.items()}
    raw, adjusted = adjusted_head_scores(outputs, policy='nonnegative_variance')
    np.testing.assert_allclose(raw, [-3., -2., -1.])
    np.testing.assert_allclose(adjusted, [0., 7/18, 1.], rtol=1e-6)


def test_model_score_policy_mismatch_is_rejected(tmp_path):
    import pandas as pd
    from sklearn.preprocessing import StandardScaler
    from analysis.transformer_config import TRANSFORMER_CONFIG
    from analysis.transformer_features import feature_columns, save_model_preprocessing, load_model_preprocessing
    config = dict(TRANSFORMER_CONFIG, feature_num='39')
    columns = feature_columns('39')
    scaler = StandardScaler().fit(pd.DataFrame(np.zeros((2, len(columns))), columns=columns))
    path = tmp_path/'model.pth'
    path.write_bytes(b'checkpoint fixture')
    save_model_preprocessing(path, scaler, columns, columns, {'000001.SZ': 0}, config,
        '2024-01-02', {'000001.SZ': '2024-01-02'})
    load_model_preprocessing(path, config)
    with pytest.raises(ValueError, match='policy'):
        load_model_preprocessing(path, dict(config, score_adjustment_policy='nonnegative_variance'))
