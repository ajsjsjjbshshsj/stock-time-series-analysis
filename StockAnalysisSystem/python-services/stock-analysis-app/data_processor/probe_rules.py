"""Shared decision rule: retain a feature if ANY task beats its noise maximum."""
import numpy as np


def select_probe_features(names,three_importances,n_noise):
    if (type(n_noise) is not int or n_noise<1 or not names or len(set(names))!=len(names)
            or len(three_importances)!=3):raise ValueError('Invalid probe inputs')
    values=np.asarray(three_importances,dtype=float)
    if values.shape!=(3,len(names)+n_noise) or not np.isfinite(values).all() or np.any(values<0):
        raise ValueError('Invalid probe importances')
    thresholds=values[:,-n_noise:].max(axis=1)
    keep=np.any(values[:,:len(names)]>thresholds[:,None],axis=0)
    return [name for name,selected in zip(names,keep) if selected],[int(v) for v in thresholds]
