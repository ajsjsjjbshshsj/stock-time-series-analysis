"""Opaque, controlled imports: never accept client filesystem paths or binaries."""
import json,shutil
import numpy as np
from sqlalchemy import select
from tasks.repository import tasks
from tasks.artifacts import task_path,read_json,file_digest,write_json,verify_candidate
from .training import verify_native


def bind_import(request,source):
    if request['import_task_id']:
        parent_id=request['import_task_id'];folder=task_path(source.store.root,parent_id)
        sha=file_digest(folder/'manifest.json')
        expected='xgboost_single' if request['algorithm']=='xgboost' else 'lightgbm_all_market'
    else:
        folder=task_path(source.store.root.parent/'validation_experiments',request['experiment_id'])
        parent_id=read_json(folder/'binding.json')['original_task_id'];sha=file_digest(folder/'manifest.json');expected='lightgbm_all_market'
    with source.engine.connect() as c:row=c.execute(select(tasks).where(tasks.c.id==parent_id)).mappings().one()
    original=dict(id=row['id'],request=json.loads(row['request']),binding=json.loads(row['binding']))
    if row['state']!='SUCCEEDED' or original['request']['kind']!=expected or request['algorithm']=='transformer':
        raise ValueError('Successful native engineering or validation source required')
    return dict(import_manifest_sha256=sha,original_task=original,data=original['binding'])


def import_operation(task,directory,source,emit):
    r=task['request'];binding=task['binding'];original=binding['original_task'];algorithm=r['algorithm']
    emit('stage',stage='INPUT')
    if r['experiment_id']:
        from analysis.market_validation import verify_validation
        old=task_path(source.store.root.parent/'validation_experiments',r['experiment_id'])
        report=verify_validation(old);protocol=read_json(old/'preregistration.json')
        parent=verify_candidate(original,source.store.root)
        parent_folder=source.store.path(original['id']);parent_meta=read_json(parent_folder/'metadata.json')
        research_binding=read_json(old/'binding.json')
        if (research_binding['original_candidate_manifest_sha256']!=parent['manifest_sha256']
                or research_binding['original_probe_sha256']!=file_digest(parent_folder/'probe.json')
                or research_binding['training_binding']!=original['binding']
                or protocol['feature_names']!=parent_meta['feature_names']
                or research_binding['original_train_only_probe']!=parent_meta['probe']):
            raise ValueError('Research import original-candidate lineage mismatch')
        trial=next(t for t in report['trials'] if t['name']==r['trial']);path=old/r['trial']
        proof=read_json(path/'reload.json');names=protocol['feature_names']
        native=dict(feature_names=names,feature_count=len(names),score_units='within_day_centered_rank' if trial['config']['label']=='daily_rank' else 'raw_return_5d',
            horizon=5,holding_sessions=5,train_start=protocol['partitions']['train']['signal_start'],train_end=protocol['partitions']['train']['signal_end'],
            seen_through=protocol['partitions']['compare']['label_end'],pool_count=original['binding']['pool_count'],metrics=trial['metrics'],
            probe_selected=True,params=trial['params'],rounds=trial['rounds'],original_experiment_id=r['experiment_id'],original_trial=r['trial'])
        native['metrics']={k:v for k,v in native['metrics'].items() if k!='daily'}
        shutil.copyfile(path/'model.txt',directory/'model.txt');write_json(directory/'reload.json',dict(values=proof['values'],predictions=proof['predictions']))
        verify_validation(old)
        if verify_candidate(original,source.store.root)!=parent:raise ValueError('Research parent changed')
    else:
        verify_candidate(original,source.store.root);old=source.store.path(original['id']);meta=read_json(old/'metadata.json');proof=read_json(old/'probe.json')
        names=meta['feature_names'];market=algorithm=='lightgbm'
        native=dict(feature_names=names,feature_count=len(names),score_units='raw_return_5d' if market else 'probability_up_1d',
            horizon=5 if market else 1,holding_sessions=5 if market else 1,
            train_start=meta['splits']['train']['signal_start'] if market else meta['training']['splits']['train']['signal_start'],
            train_end=meta['splits']['train']['signal_end'] if market else meta['training']['splits']['train']['signal_end'],
            seen_through=meta['splits']['val']['label_end'] if market else meta['training']['seen_through'],
            pool_count=original['binding'].get('pool_count',1),metrics=meta['metrics'],probe_selected=market,params=meta['params'],original_task_id=original['id'])
        filename='model.txt' if market else 'model.json';shutil.copyfile(old/filename,directory/filename)
        if not market:
            from xgboost import XGBClassifier
            import pandas as pd
            model=XGBClassifier();model.load_model(directory/filename)
            proof=dict(values=proof['values'],predictions=model.predict_proba(pd.DataFrame(proof['values'],columns=names))[:,1].tolist())
        write_json(directory/'reload.json',proof);verify_candidate(original,source.store.root)
    native.update(algorithm=algorithm,ts_code=original['request'].get('ts_code'),stock_codes=None)
    write_json(directory/'native.json',native);verify_native(directory,algorithm)
    return {k:v for k,v in native.items() if k not in ('params','algorithm','ts_code','stock_codes')}
