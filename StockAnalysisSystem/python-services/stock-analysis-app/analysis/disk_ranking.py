"""The existing five-market-day regression task with disk-backed inputs."""
import numpy as np
import lightgbm as lgb
from tasks.disk_matrix import DiskFeatureSequence
from tasks.market_profile import parameters


def train_ranking_matrix(bundle,selected_names,profile,emit):
    if (not selected_names or len(set(selected_names))!=len(selected_names)
            or selected_names!=[name for name in bundle.feature_names if name in selected_names]):
        raise ValueError('Selected feature order invalid')
    params=parameters(profile,'regression');columns=[bundle.feature_names.index(name) for name in selected_names]
    def sequence(phase):return DiskFeatureSequence(bundle.features,bundle.splits[phase],columns)
    train=lgb.Dataset(sequence('train'),label=bundle.labels[bundle.splits['train']],feature_name=selected_names,params=params)
    val=lgb.Dataset(sequence('val'),label=bundle.labels[bundle.splits['val']],reference=train,feature_name=selected_names,params=params)
    def progress(environment):emit('progress',phase='TRAIN',completed=environment.iteration+1,total=profile['final_rounds'])
    model=lgb.train(params,train,num_boost_round=profile['final_rounds'],valid_sets=[val],
                    callbacks=[lgb.early_stopping(10,verbose=False),lgb.log_evaluation(0),progress])
    index=bundle.splits['test'];seq=sequence('test');squared=0.;correct=0
    for start in range(0,len(seq),4096):
        end=min(start+4096,len(seq));scores=np.asarray(model.predict(seq[start:end],num_threads=profile['num_threads']))
        labels=bundle.labels[index[start:end]]
        if scores.shape!=(end-start,) or not np.isfinite(scores).all():raise ValueError('Invalid regression predictions')
        squared+=float(np.dot(scores-labels,scores-labels));correct+=int((np.sign(scores)==np.sign(labels)).sum())
    metrics=dict(RMSE=float(np.sqrt(squared/len(seq))),Direction_Accuracy=correct/len(seq))
    metadata=dict(params=params,feature_names=selected_names,horizon=5,rounds=model.current_iteration(),splits=bundle.split_metadata,
                  purpose='engineering_smoke',published=False)
    return model,metadata,metrics
