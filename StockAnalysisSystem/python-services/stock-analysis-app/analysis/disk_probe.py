"""Train-only shadow selection over bounded disk sequences, not a market panel."""
import gc
from tempfile import TemporaryDirectory
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
from data_processor.probe_rules import select_probe_features
from tasks.disk_matrix import DiskFeatureSequence
from tasks.market_profile import parameters


class AugmentedSequence(lgb.Sequence):
    batch_size=4096

    def __init__(self,bundle,index,columns,noise):
        self.features=DiskFeatureSequence(bundle.features,index,columns)
        self.noise=DiskFeatureSequence(noise,index,list(range(noise.shape[1])))

    def __len__(self):return len(self.features)

    def __getitem__(self,index):
        return np.concatenate((self.features[index],self.noise[index]),axis=-1)


def disk_probe(bundle,feature_names,profile,emit):
    params=parameters(profile,'regression',probe=True)
    if feature_names!=bundle.feature_names:raise ValueError('Probe feature order mismatch')
    outer=bundle.splits['train'];dates=bundle.dates[outer];unique=np.unique(dates);split=int(len(unique)*.9)
    if not 0<split<len(unique):raise ValueError('Insufficient probe dates')
    boundary=unique[split]
    train=outer[(dates<boundary)&(bundle.target_dates[outer]<boundary)];val=outer[dates>=boundary]
    if not len(train) or not len(val):raise ValueError('Empty purged probe split')
    # Only compact outer-train labels are grouped; no outer validation/test data.
    y=bundle.labels[outer]
    median=pd.DataFrame(dict(date=dates,label=y)).groupby('date').label.transform('median').to_numpy()
    position={int(row):i for i,row in enumerate(outer)}
    ti=np.asarray([position[int(row)] for row in train]);vi=np.asarray([position[int(row)] for row in val])
    labels=[(y>median).astype(np.int8),y,(y>0).astype(np.int8)]
    for i in (0,2):
        if len(np.unique(labels[i][ti]))!=2 or len(np.unique(labels[i][vi]))!=2:
            raise ValueError('Probe requires both binary classes')
    record=dict(iterations=1,noise_count=profile['n_noise'],outer_train_rows=len(outer),
                inner_train_rows=len(train),inner_val_rows=len(val),
                inner_train_target_end=int(bundle.target_dates[train].max()),inner_val_start=int(boundary),tasks=[])
    importances=[];columns=list(range(len(feature_names)))
    with TemporaryDirectory(prefix='probe-',dir=bundle.directory) as temporary:
        noise=np.memmap(Path(temporary)/'noise.f32',mode='w+',dtype='<f4',shape=(bundle.rows,profile['n_noise']))
        rng=np.random.default_rng(profile['seed'])
        try:
            for start in range(0,bundle.rows,4096):
                stop=min(start+4096,bundle.rows)
                noise[start:stop]=rng.standard_normal((stop-start,profile['n_noise'])).astype(np.float32)
            noise.flush()
            names=[*feature_names,*['__noise_'+str(i) for i in range(profile['n_noise'])]]
            # Construct and free one pair at a time; no three raw market copies.
            for i,task in enumerate(('classification','regression','direction')):
                params=parameters(profile,'regression' if i==1 else 'binary',probe=True)
                ds=lgb.Dataset(AugmentedSequence(bundle,train,columns,noise),label=labels[i][ti],feature_name=names,params=params)
                valid=lgb.Dataset(AugmentedSequence(bundle,val,columns,noise),label=labels[i][vi],reference=ds,feature_name=names,params=params)
                model=lgb.train(params,ds,num_boost_round=profile['probe_rounds'],valid_sets=[valid],
                                callbacks=[lgb.early_stopping(15,verbose=False),lgb.log_evaluation(0)])
                importances.append(model.feature_importance(importance_type='split'))
                record['tasks'].append(dict(task=task,rounds=model.current_iteration(),num_threads=profile['num_threads']))
                del model,ds,valid;gc.collect()
                emit('progress',phase='PROBE',completed=i+1,total=3)
        finally:noise._mmap.close()
    selected,thresholds=select_probe_features(feature_names,importances,profile['n_noise'])
    if not selected:raise ValueError('Probe selected no features')
    record.update(selected_features=selected,thresholds=thresholds,importances=[v.tolist() for v in importances])
    return selected,record
