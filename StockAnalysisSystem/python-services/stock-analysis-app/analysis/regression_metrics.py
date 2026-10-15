"""Finite paired return metrics shared by forecasting and retrospective evaluation."""
import numpy as np


def metrics(actual, predicted):
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if (actual.ndim != 1 or actual.size == 0 or actual.shape != predicted.shape
            or not np.isfinite(actual).all() or not np.isfinite(predicted).all()):
        raise ValueError('Metrics require nonempty finite paired observations')
    residual = actual - predicted
    constant = actual.size < 2 or np.all(actual == actual[0])
    denominator = 0. if constant else float(np.sum((actual - np.mean(actual)) ** 2))
    result = dict(rmse=float(np.sqrt(np.mean(residual ** 2))), mae=float(np.mean(abs(residual))),
        r2=float(1 - np.sum(residual ** 2) / denominator) if denominator else None)
    if any(value is not None and not np.isfinite(value) for value in result.values()):
        raise ValueError('Nonfinite metrics')
    return result
