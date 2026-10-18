"""Server-owned engineering limits, never browser-supplied hyperparameters."""


def bounded_profile():
    return dict(name='bounded_v1',seed=42,num_threads=2,n_noise=10,probe_iterations=1,
                probe_rounds=30,final_rounds=20,max_bin=63,
                min_available_bytes=8*1024**3,min_disk_bytes=16*1024**3,
                max_tree_rss_bytes=6*1024**3,min_runtime_disk_bytes=2*1024**3,max_seconds=3600)


def parameters(profile,objective,*,probe=False):
    if profile!=bounded_profile():raise ValueError('Unknown engineering profile')
    return dict(objective=objective,metric='auc' if objective=='binary' else 'rmse',
                seed=profile['seed'],num_threads=profile['num_threads'],device_type='cpu',
                deterministic=True,force_col_wise=True,feature_pre_filter=False,
                max_bin=profile['max_bin'],num_leaves=31 if probe else 63,learning_rate=.05,
                feature_fraction=1.,bagging_fraction=1.,bagging_freq=0,verbosity=-1)
