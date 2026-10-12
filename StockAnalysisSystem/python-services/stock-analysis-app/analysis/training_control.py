"""Small state machine shared by checkpoint selection and optional early stop."""
import math


class EarlyStopping:
    def __init__(self, patience=0, min_delta=0.):
        if type(patience) is not int or patience < 0 or not math.isfinite(min_delta) or min_delta < 0:
            raise ValueError('Invalid early stopping settings')
        self.patience = patience
        self.min_delta = min_delta
        self.best = -math.inf
        self.wait = 0

    def update(self, score):
        if not math.isfinite(score):
            raise ValueError('Validation score must be finite')
        improved = score > self.best + self.min_delta
        if improved:
            self.best, self.wait = score, 0
        else:
            self.wait += 1
        return improved, bool(self.patience and self.wait >= self.patience)
