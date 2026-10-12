"""Versioned cross-sectional scores, shared by offline evaluation and inference.

Normalized scores are ranking utilities, not probabilities or predicted returns.
Legacy remains the default for models without an explicit policy.
"""
import numpy as np

POLICIES = ('legacy_variance', 'nonnegative_variance')


def score_policy(config):
    policy = config.get('score_adjustment_policy', 'legacy_variance')
    if policy not in POLICIES:
        raise ValueError(f'Unknown score adjustment policy: {policy}')
    return policy


def _normalize(values):
    width = values.max() - values.min()
    if not np.isfinite(width):
        raise ValueError('Score vector range must be finite')
    return np.zeros_like(values) if width < 1e-9 else (values - values.min()) / width


def adjust_scores(ranking, regression, classification, direction, policy='legacy_variance'):
    """Discount nonnegative rank utility so disagreement never promotes a score."""
    score_policy({'score_adjustment_policy': policy})
    vectors = [np.asarray(value) for value in (ranking, regression, classification, direction)]
    vectors = [value if value.dtype.kind == 'f' else value.astype(float) for value in vectors]
    if any(value.ndim != 1 or not value.size or value.shape != vectors[0].shape
           or not np.isfinite(value).all() for value in vectors):
        raise ValueError('Score vectors must be nonempty, finite and matching shape')
    uncertainty = np.stack([_normalize(value) for value in vectors[1:]]).var(axis=0)
    base = vectors[0] if policy == 'legacy_variance' else _normalize(vectors[0])
    return base * (1 - uncertainty), uncertainty
