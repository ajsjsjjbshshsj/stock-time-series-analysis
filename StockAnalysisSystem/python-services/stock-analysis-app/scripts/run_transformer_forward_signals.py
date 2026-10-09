"""One-shot collection and immutable frozen signals. Never train, deploy or trade."""
import argparse
from datetime import datetime,timedelta
import os
from pathlib import Path
import subprocess
import sys

APP=Path(__file__).resolve().parents[1]
COLLECTOR=APP.parent/'python-collector'
sys.path.insert(0,str(APP))
os.environ.setdefault('MPLBACKEND','Agg')
os.environ.setdefault('OMP_NUM_THREADS','8')

import pandas as pd
from analysis.transformer_experiment import sha256,load_source
from analysis.transformer_features import load_model_preprocessing
from analysis.transformer_forward_contracts import (TZ,market_frame,frame_hash,validate_calendar,select_signal_date,
    session_dates,validate_history,forward_model_panel,iso_day)
from analysis.transformer_forward_inference import score_session
from analysis.transformer_forward_store import ForwardSignalStore,read,write_exclusive,identity


def now(): return datetime.now(TZ)


def validate_paths(freeze_dir,output):
    freeze_dir,output=Path(freeze_dir).resolve(),Path(output).resolve()
    if (output.parent!=APP/'models/transformer' or not output.name.startswith('forward_signals_')
            or not freeze_dir.name.startswith('forward_freeze_')
            or output.is_relative_to(freeze_dir) or freeze_dir.is_relative_to(output)):
        raise ValueError('Use a separate approved forward_signals_* output and frozen group')
    return freeze_dir,output


def load_frozen(freeze_dir):
    """Invoke the strict read-only freeze audit; never its training or writing stage."""
    from scripts.run_transformer_forward_freeze import verify as verify_frozen
    manifest=read(freeze_dir/'freeze_manifest.json')
    upstream=manifest['binding']['upstream']
    verify_frozen(Path(upstream['source']),freeze_dir)
    fixed=Path(upstream['fixed_binding']['source'])
    original=Path(read(fixed/'source_identity.json')['source'])
    raw_path=Path(read(original/'source_identity.json')['source'])
    daily,_=load_source(raw_path)
    factor_path=original/'factors.parquet'
    factors=pd.read_parquet(factor_path)
    models=manifest['models']
    _,metadata=load_model_preprocessing(models[0]['model_path'],models[0]['config'])
    identities=[]
    for model in models:
        model_path=Path(model['model_path'])
        identities.append(dict(seed=model['seed'],mode=model['mode'],model_sha256=sha256(model_path),
            scaler_sha256=sha256(model['scaler_path']),config_sha256=sha256(model_path.parent/'config.json'),
            preprocessing_sha256=sha256(model_path.with_name(model_path.stem+'_preprocessing.json'))))
    binding=dict(freeze_dir=str(freeze_dir),freeze_sha256=sha256(freeze_dir/'freeze_manifest.json'),
                 cutoff=manifest['binding']['cutoff'],frozen_at=manifest['frozen_at'],
                 codes=manifest['binding']['codes'],rules=manifest['rules'],models=identities,
                 training_daily_sha256=sha256(raw_path),training_factor_sha256=sha256(factor_path))
    return dict(daily=daily,factors=factors,models=models,metadata=metadata,binding=binding)


def collect(stage,request,output):
    """Provider credentials stay exclusively in the collector subprocess."""
    output=Path(output)
    output.mkdir(parents=True,exist_ok=True)
    contract=output/'contract.json'
    if contract.exists():
        if read(contract)!=request:
            raise ValueError('Collector request identity differs from existing evidence')
    else:
        write_exclusive(contract,request)
    command=[sys.executable,str(COLLECTOR/'scripts/collect_forward_snapshot.py'),'--stage',stage,
             '--contract',str(contract),'--output',str(output)]
    result=subprocess.run(command,cwd=COLLECTOR,capture_output=True,text=True,encoding='utf-8',errors='replace')
    if result.returncode:
        # Never propagate raw SDK output or exception text, which may contain credentials.
        raise RuntimeError(f'COLLECTOR_FAILED {stage}: safe evidence retained in acquisition directory')


def _old_hashes(output):
    root=APP/'models'
    return {p.relative_to(root).as_posix():sha256(p) for p in sorted(root.rglob('*'))
            if p.is_file() and not p.is_relative_to(output)}


def _check_old(output):
    hashes=read(output/'old_artifact_hashes_before.json')
    root=(APP/'models').resolve()
    for name,digest in hashes.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256(path)!=digest:
            raise ValueError('Old model/cache evidence changed or escaped its root')
    return len(hashes)


def _store(output,frozen,initialize=False):
    old_path=output/'old_artifact_hashes_before.json'
    if old_path.exists():
        hashes=read(old_path)
    elif initialize and not output.exists():
        hashes=_old_hashes(output)
    else:
        raise ValueError('Partial or missing output identity; preserve existing evidence')
    binding=dict(frozen['binding'],old_artifacts_sha256=identity(hashes))
    store=ForwardSignalStore(output,binding)
    if not old_path.exists():
        write_exclusive(old_path,hashes)
    _check_old(output)
    return store


def _calendar_file(path):
    body=read(path/'calendar.json')
    manifest=read(path/'calendar_manifest.json')
    if manifest.get('calendar_sha256')!=sha256(path/'calendar.json'):
        raise ValueError('Calendar snapshot hash changed')
    validate_calendar(body)
    return body


def _calendar(output,frozen,requested=None):
    clock=now()
    start=str(pd.to_datetime(frozen['daily'].trade_date).min().date())
    probe=requested or str(clock.date())
    iso_day(probe)
    for path in sorted((output/'calendars').glob('*'),reverse=True):
        body=_calendar_file(path)
        if body['start']<=start and str(clock.date())<=body['end'] and probe<=body['end']:
            completed=select_signal_date(body,clock,requested,frozen['binding']['cutoff'])
            if completed is None or session_dates(body,completed):
                return body,path
    end=str((max(clock.date(),datetime.fromisoformat(probe).date())+timedelta(days=30)))
    path=output/'calendars'/f'{start}_{end}'
    collect('calendar',dict(start=start,end=end),path)
    return _calendar_file(path),path


def _acquisition(output,signal_date,calendar_sha,frozen,calendar,fetch=False):
    opened=validate_calendar(calendar)
    dates=[d for d in opened if frozen['binding']['cutoff']<d<=signal_date]
    binding=dict(frozen['binding'],signal_date=signal_date,calendar_sha256=calendar_sha)
    request=dict(codes=frozen['binding']['codes'],dates=dates,binding=binding)
    path=output/'acquisitions'/signal_date
    if fetch and not (path/'acquisition_manifest.json').exists():
        collect('market',request,path)
    manifest=read(path/'acquisition_manifest.json')
    if (any(manifest.get(k)!=v for k,v in request.items()) or manifest.get('source')!='tushare'
            or manifest.get('volume_unit')!='lots' or manifest.get('amount_unit')!='thousand_CNY'
            or manifest.get('daily_sha256')!=sha256(path/'daily.parquet')
            or manifest.get('factor_sha256')!=sha256(path/'factors.parquet')):
        raise ValueError('Actual acquisition identity/units/files mismatch')
    daily=market_frame(pd.read_parquet(path/'daily.parquet'))
    factors=market_frame(pd.read_parquet(path/'factors.parquet'),True)
    for frame,key in ((daily,'daily_rows'),(factors,'factor_rows')):
        if len(frame)!=manifest.get(key) or set(frame.ts_code)!=set(request['codes']):
            raise ValueError('Acquisition universe/count mismatch')
        if any(frame.loc[frame.ts_code==c,'trade_date'].tolist()!=dates for c in request['codes']):
            raise ValueError('Acquisition full date coverage mismatch')
    return daily,factors,dict(directory=path.relative_to(output).as_posix(),
                            manifest_sha256=sha256(path/'acquisition_manifest.json'),
                            daily_sha256=manifest['daily_sha256'],factor_sha256=manifest['factor_sha256'])


def _inputs(frozen,daily,factors,calendar,signal_date,acquisition):
    old_daily,old_factors=market_frame(frozen['daily']),market_frame(frozen['factors'],True)
    combined=pd.concat([old_daily,daily],ignore_index=True)
    combined_factors=pd.concat([old_factors,factors],ignore_index=True)
    proof=validate_history(old_daily,old_factors,combined,combined_factors,calendar,signal_date,frozen['metadata'])
    provenance=dict(training_factor_sha256=frozen['binding']['training_factor_sha256'],
                    combined_factor_sha256=frame_hash(combined_factors),acquisition_factor_sha256=acquisition['factor_sha256'])
    panels,proofs={},{}
    for record in frozen['models']:
        mode=record['mode']
        if mode not in panels:
            panels[mode]=forward_model_panel(combined,combined_factors,record['config']['market_preprocessing'],provenance)
            proofs[mode]=dict(proof,model_panel_sha256=frame_hash(panels[mode]),factor_provenance=provenance)
    return panels,proofs


def _prior_forward_history(store,output,signal_date,daily,factors):
    """An accepted forward packet extends the immutable historical prefix too."""
    for folder in sorted((output/'signals').glob('*')):
        if not folder.is_dir() or folder.name>=signal_date or not (folder/'seal.json').is_file():
            continue  # An incomplete prior attempt is evidence, not an accepted prefix.
        previous=iso_day(folder.name)
        store.verify(previous)
        payload=read(folder/'payload.json')
        proof=payload['acquisition']
        directory=output/'acquisitions'/previous
        if proof['directory']!=directory.relative_to(output).as_posix():
            raise ValueError('Prior published acquisition path changed')
        for name,key,current,factor in (('daily.parquet','daily_sha256',daily,False),
                                        ('factors.parquet','factor_sha256',factors,True)):
            path=directory/name
            if sha256(path)!=proof[key]:
                raise ValueError('Prior published acquisition evidence changed')
            prefix=market_frame(pd.read_parquet(path),factor)
            retained=current[current.trade_date.isin(prefix.trade_date)].reset_index(drop=True)
            if not retained.equals(prefix):
                raise ValueError('New acquisition revises prior published forward-history prefix')


def verify(freeze_dir,output,signal_date):
    freeze_dir,output=validate_paths(freeze_dir,output)
    frozen=load_frozen(freeze_dir)
    store=_store(output,frozen)
    result=store.verify(signal_date)
    payload=read(output/'signals'/signal_date/'payload.json')
    calendar_path=(output/payload['calendar_directory']).resolve()
    if not calendar_path.is_relative_to(output/'calendars'):
        raise ValueError('Calendar proof path escapes its owned directory')
    calendar=_calendar_file(calendar_path)
    if payload['calendar_sha256']!=sha256(calendar_path/'calendar.json'):
        raise ValueError('Published calendar differs from acquisition evidence')
    receipt=read(output/'signals'/signal_date/'receipt.json')
    if receipt['calendar']!=calendar:
        raise ValueError('Published timing calendar differs from actual calendar file')
    daily,factors,acquisition=_acquisition(output,signal_date,payload['calendar_sha256'],frozen,calendar)
    _prior_forward_history(store,output,signal_date,daily,factors)
    if payload['acquisition']!=acquisition:
        raise ValueError('Published acquisition evidence mismatch')
    _,proofs=_inputs(frozen,daily,factors,calendar,signal_date,acquisition)
    for record in payload['models']:
        if record.get('history_proof')!=proofs[record['mode']]:
            raise ValueError('Published full-history evidence differs from actual retained input')
    result['old_files_checked']=_check_old(output)
    return result


def run(freeze_dir,output,signal_date=None):
    freeze_dir,output=validate_paths(freeze_dir,output)
    frozen=load_frozen(freeze_dir)
    store=_store(output,frozen,initialize=True)
    if signal_date is not None:
        iso_day(signal_date)
        if (output/'signals'/signal_date).exists():
            return verify(freeze_dir,output,signal_date)
    calendar,calendar_path=_calendar(output,frozen,signal_date)
    selected=select_signal_date(calendar,now(),signal_date,frozen['binding']['cutoff'])
    if selected is None:
        return dict(status='NO_NEW_SESSION',models=0,prospective_eligible=False,old_files_checked=_check_old(output))
    session_dates(calendar,selected)
    with store.lock(selected):
        if (output/'signals'/selected).exists():
            return verify(freeze_dir,output,selected)
        attempt=store.new_attempt(selected)
        calendar_sha=sha256(calendar_path/'calendar.json')
        daily,factors,acquisition=_acquisition(output,selected,calendar_sha,frozen,calendar,fetch=True)
        _prior_forward_history(store,output,selected,daily,factors)
        panels,proofs=_inputs(frozen,daily,factors,calendar,selected,acquisition)
        records=[]
        for record in frozen['models']:
            print(f"FORWARD_SCORE seed={record['seed']} mode={record['mode']} date={selected}",flush=True)
            rows=score_session(panels[record['mode']],record,selected,proofs[record['mode']])
            records.append(dict(seed=record['seed'],mode=record['mode'],scores=rows,history_proof=proofs[record['mode']]))
        payload=dict(signal_date=selected,models=records,acquisition=acquisition,calendar_sha256=calendar_sha,
                     calendar_directory=calendar_path.relative_to(output).as_posix())
        store.publish(selected,attempt,payload,calendar=calendar,
                      frozen_at=datetime.fromisoformat(frozen['binding']['frozen_at']),clock=now)
        return verify(freeze_dir,output,selected)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('run','verify'),required=True)
    parser.add_argument('--freeze-dir',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--signal-date')
    args=parser.parse_args(argv)
    if args.stage=='verify' and not args.signal_date:
        parser.error('--signal-date is required for verify')
    try:
        result=(run if args.stage=='run' else verify)(args.freeze_dir,args.output,args.signal_date)
        print('FORWARD_RESULT '+str(result['status'])+' date='+str(result.get('signal_date','none'))
              +' old_files_unchanged='+str(result['old_files_checked']),flush=True)
        return 0
    except Exception:
        print('FORWARD_FAILED: preserve existing evidence; check frozen source, calendar, quota and input completeness.',file=sys.stderr)
        return 1


if __name__=='__main__':sys.exit(main())
