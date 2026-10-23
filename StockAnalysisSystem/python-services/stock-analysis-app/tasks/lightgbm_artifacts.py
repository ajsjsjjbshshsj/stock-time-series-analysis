"""Exact native LightGBM candidate format, separate from XGBoost and releases."""
import re
import numpy as np
from .artifacts import task_path,task_digest,read_json,file_digest,_is_link
from .contracts import encoded,digest
from .market_profile import bounded_profile,parameters
from .resources import MAX_EXECUTION_SECONDS

FILES=('model.txt','metadata.json','probe.json','coverage.json')


def _verify_semantics(task,metadata,coverage):
    """Independently reconstruct date accounting and purge/probe boundaries."""
    from data_processor.feature_store_contract import FEATURE_COLUMNS
    from data_processor.probe_rules import select_probe_features
    from .market_samples import open_days
    from .market_lifecycle import validate_lifecycle,inactive_reason
    calendar=coverage['calendar'];binding=task['binding'];request=task['request']
    if digest(calendar)!=binding['calendar_sha256']:raise ValueError('Candidate calendar identity mismatch')
    opened=[str(day) for day in open_days(calendar)];positions={day:i for i,day in enumerate(opened)}
    requested=[day for day in opened if request['start_date']<=day<=request['end_date']]
    daily=coverage['daily_eligible_stocks'];count=len(requested)
    if not count or set(daily)!=set(requested):raise ValueError('Incomplete eligibility dates')
    indexes={day:i for i,day in enumerate(requested)};difference=np.zeros(count+1,dtype=np.int64)
    difference[0]=len(binding['stock_codes'])
    inactive={'PRE_LISTING','POST_DELISTING','OUTSIDE_CODE_PERIOD'}
    allowed=inactive|{'SUSPENDED','WARMUP','UNMATURED_LABEL','TARGET_SUSPENDED','TARGET_INACTIVE'}
    lifecycles=binding['lifecycle']['stocks']
    if set(lifecycles)!=set(binding['stock_codes']):raise ValueError('Incomplete lifecycle binding')
    for code in binding['stock_codes']:
        row=coverage['stocks'][code];reasons=row['reasons'];ranges=row['excluded_ranges']
        lifecycle=validate_lifecycle(code,lifecycles[code])
        if row['lifecycle']!=lifecycle or row['list_date']!=lifecycle['list_date']:raise ValueError('Candidate lifecycle mismatch')
        if (row['ts_code']!=code or row['requested_open_days']!=count or set(reasons)!=set(ranges)
                or not set(reasons)<=allowed or type(row['eligible_rows']) is not int or not 0<=row['eligible_rows']<=count
                or type(row['observed_rows']) is not int or not 0<=row['observed_rows']<=count
                or any(type(v) is not int or v<=0 for v in reasons.values())
                or sum(reasons.values())+row['eligible_rows']!=count):raise ValueError('Invalid per-stock coverage')
        observed=row['eligible_rows']+sum(reasons.get(k,0) for k in ('WARMUP','UNMATURED_LABEL','TARGET_SUSPENDED','TARGET_INACTIVE'))
        if row['observed_rows']!=observed:raise ValueError('Observed rows conflict with eligibility accounting')
        occupied=np.zeros(count,dtype=bool)
        for reason,segments in ranges.items():
            total=0;last=-1
            for first,end in segments:
                if first not in indexes or end not in indexes:raise ValueError('Exclusion outside requested calendar')
                a,b=indexes[first],indexes[end]
                if a>b or a<=last or occupied[a:b+1].any():raise ValueError('Overlapping exclusion ranges')
                for day in requested[a:b+1]:
                    status=inactive_reason(day,lifecycle)
                    if (reason in inactive and status!=reason or reason not in inactive and status is not None):
                        raise ValueError('Exclusion conflicts with lifecycle')
                    target_pos=positions[day]+5
                    matured=target_pos<len(opened) and opened[target_pos]<=request['end_date']
                    target_inactive=matured and inactive_reason(opened[target_pos],lifecycle) is not None
                    if reason=='TARGET_INACTIVE' and not target_inactive:raise ValueError('Invalid target lifecycle exclusion')
                    if reason=='TARGET_SUSPENDED' and (not matured or target_inactive):raise ValueError('Invalid target suspension exclusion')
                    if reason=='UNMATURED_LABEL' and matured:raise ValueError('Mature target classified as unavailable')
                occupied[a:b+1]=True;last=b;total+=b-a+1;difference[a]-=1;difference[b+1]+=1
            if total!=reasons[reason]:raise ValueError('Exclusion count mismatch')
        for i,day in enumerate(requested):
            if inactive_reason(day,lifecycle) and not occupied[i]:raise ValueError('Inactive date counted as eligible')
            target_pos=positions[day]+5
            if (not occupied[i] and (target_pos>=len(opened) or opened[target_pos]>request['end_date']
                    or inactive_reason(opened[target_pos],lifecycle))):raise ValueError('Ineligible label target')
    expected=np.cumsum(difference[:-1])
    if any(type(daily[day]) is not int or daily[day]!=int(expected[i]) for i,day in enumerate(requested)):
        raise ValueError('Daily and per-stock eligibility differ')
    eligible=[day for day in requested if daily[day]];n=len(eligible);first=int(n*.6);second=first+int(n*.2)
    if not 0<first<second<n:raise ValueError('Insufficient candidate dates')
    def target(day):
        index=positions[day]+5
        if index>=len(opened) or opened[index]>request['end_date']:raise ValueError('Unmatured candidate target')
        return opened[index]
    val_start,test_start=eligible[first],eligible[second]
    groups=dict(train=[d for d in eligible if d<val_start and target(d)<val_start],
        val=[d for d in eligible if val_start<=d<test_start and target(d)<test_start],test=[d for d in eligible if d>=test_start])
    expected_splits={}
    for phase,days in groups.items():
        if not days:raise ValueError('Empty candidate split')
        expected_splits[phase]=dict(count=sum(daily[d] for d in days),signal_start=days[0],signal_end=days[-1],label_end=target(days[-1]))
    if metadata['splits']!=expected_splits:raise ValueError('Candidate purge/split evidence mismatch')
    probe=metadata['probe'];train=groups['train'];split=int(len(train)*.9)
    if not 0<split<len(train):raise ValueError('Insufficient inner probe dates')
    boundary=train[split];inner=[d for d in train if d<boundary and target(d)<boundary];val=[d for d in train if d>=boundary]
    ordinal=lambda value:int(np.datetime64(value,'D').astype(np.int64))
    if (not inner or not val or probe['iterations']!=1 or probe['noise_count']!=10
            or probe['outer_train_rows']!=expected_splits['train']['count']
            or probe['inner_train_rows']!=sum(daily[d] for d in inner) or probe['inner_val_rows']!=sum(daily[d] for d in val)
            or probe['inner_train_target_end']!=ordinal(target(inner[-1])) or probe['inner_val_start']!=ordinal(boundary)):
        raise ValueError('Candidate inner probe evidence mismatch')
    tasks=probe['tasks']
    if (not isinstance(tasks,list) or len(tasks)!=3
            or [r['task'] for r in tasks]!=['classification','regression','direction']
            or any(set(r)!={'task','rounds','num_threads'} or type(r['rounds']) is not int or not 1<=r['rounds']<=30
                   or type(r['num_threads']) is not int or r['num_threads']!=2 for r in tasks)):
        raise ValueError('Candidate probe profile mismatch')
    selected,thresholds=select_probe_features(list(FEATURE_COLUMNS),probe['importances'],10)
    if not selected or selected!=metadata['feature_names'] or probe['thresholds']!=thresholds:
        raise ValueError('Candidate probe selection mismatch')


def validate_summary(value,task):
    expected={'task_id','kind','purpose','verified','published','manifest_sha256','files','metrics','coverage','resources'}
    if (not isinstance(value,dict) or set(value)!=expected or value['task_id']!=task['id']
            or value['kind']!='lightgbm_all_market' or value['purpose']!='engineering_smoke'
            or value['verified'] is not True or value['published'] is not False or len(encoded(value).encode())>8192):
        raise ValueError('Invalid market candidate summary')
    hashes=[value['manifest_sha256'],*value['files'].values()]
    if set(value['files'])!=set(FILES) or any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes):
        raise ValueError('Invalid candidate hashes')
    coverage=value['coverage'];binding=task['binding']
    if (coverage['pool_count']!=binding['pool_count'] or coverage['pool_identity']!=binding['pool_identity']
            or set(coverage)!= {'pool_count','pool_identity','eligible_rows','purged_rows','zero_eligible_stocks','splits'}
            or any(type(coverage[k]) is not int or coverage[k]<0 for k in ('eligible_rows','purged_rows','zero_eligible_stocks'))
            or not coverage['eligible_rows'] or coverage['zero_eligible_stocks']>coverage['pool_count']
            or set(coverage['splits'])!={'train','val','test'}
            or any(type(v) is not int or v<=0 for v in coverage['splits'].values())
            or sum(coverage['splits'].values())+coverage['purged_rows']!=coverage['eligible_rows']):
        raise ValueError('Invalid candidate coverage')
    metrics=value['metrics'];resources=value['resources'];profile=bounded_profile()
    if (set(metrics)!={'RMSE','Direction_Accuracy'} or any(type(v) not in (int,float) or not np.isfinite(v) for v in metrics.values())
            or metrics['RMSE']<0 or not 0<=metrics['Direction_Accuracy']<=1
            or set(resources)!={'peak_rss_bytes','elapsed_seconds'}
            or type(resources['peak_rss_bytes']) is not int or not 0<resources['peak_rss_bytes']<=profile['max_tree_rss_bytes']
            or type(resources['elapsed_seconds']) not in (int,float)
            or not 0<=resources['elapsed_seconds']<=MAX_EXECUTION_SECONDS):raise ValueError('Invalid metrics or resources')
    return value


def verify_lightgbm(task,root):
    import lightgbm as lgb
    from data_processor.feature_store_contract import FEATURE_COLUMNS
    path=task_path(root,task['id'])
    if not path.is_dir() or {p.name for p in path.iterdir()}!={*FILES,'manifest.json'}:raise ValueError('Incomplete market candidate')
    if any(_is_link(path/name) for name in (*FILES,'manifest.json')):raise ValueError('Unsafe candidate file')
    manifest=read_json(path/'manifest.json');sha=task_digest(task)
    if (manifest.get('schema_version')!=1 or manifest.get('kind')!='lightgbm_all_market' or manifest.get('task_sha256')!=sha
            or set(manifest.get('files',{}))!=set(FILES)
            or any(file_digest(path/name)!=manifest['files'][name] for name in FILES)):
        raise ValueError('Market candidate integrity mismatch')
    metadata=read_json(path/'metadata.json');probe=read_json(path/'probe.json')
    coverage=read_json(path/'coverage.json',max_bytes=16*1024*1024)
    names=metadata['feature_names'];profile=bounded_profile()
    if (metadata.get('task_sha256')!=sha or metadata.get('source')!=task['binding']
            or metadata.get('purpose')!='engineering_smoke' or metadata.get('published') is not False
            or metadata.get('params')!=parameters(profile,'regression') or metadata.get('horizon')!=5
            or not names or names!=[n for n in FEATURE_COLUMNS if n in names]
            or metadata['probe']['selected_features']!=names or not 0<metadata['rounds']<=20):
        raise ValueError('Market candidate metadata mismatch')
    codes=task['binding']['stock_codes']
    if (coverage['pool_count']!=len(codes) or coverage['pool_identity']!=digest(codes)
            or set(coverage['stocks'])!=set(codes)
            or coverage['eligible_rows']!=sum(r['eligible_rows'] for r in coverage['stocks'].values())
            or coverage['eligible_rows']!=sum(coverage['daily_eligible_stocks'].values())
            or any(type(v) is not int or not 0<=v<=len(codes) for v in coverage['daily_eligible_stocks'].values())):
        raise ValueError('Candidate pool mismatch')
    _verify_semantics(task,metadata,coverage)
    if (path/'model.txt').stat().st_size>8*1024*1024:raise ValueError('Candidate model bound exceeded')
    model=lgb.Booster(model_file=str(path/'model.txt'))
    if model.feature_name()!=names or model.current_iteration()!=metadata['rounds']:raise ValueError('Native feature mismatch')
    values=np.asarray(probe['values'],dtype=float);expected=np.asarray(probe['predictions'],dtype=float)
    if (values.ndim!=2 or not 1<=len(values)<=16 or values.shape[1]!=len(names) or not np.isfinite(values).all()
            or expected.shape!=(len(values),) or not np.isfinite(expected).all()
            or not np.allclose(model.predict(values,num_threads=2),expected,rtol=1e-12,atol=1e-12)):
        raise ValueError('Native reload probe mismatch')
    summary=dict(task_id=task['id'],kind='lightgbm_all_market',purpose='engineering_smoke',verified=True,published=False,
        manifest_sha256=file_digest(path/'manifest.json'),files=manifest['files'],metrics=metadata['metrics'],resources=metadata['resources'],
        coverage=dict(pool_count=len(codes),pool_identity=coverage['pool_identity'],eligible_rows=coverage['eligible_rows'],
            purged_rows=coverage['purged_rows'],zero_eligible_stocks=sum(r['eligible_rows']==0 for r in coverage['stocks'].values()),
            splits={k:v['count'] for k,v in metadata['splits'].items()}))
    return validate_summary(summary,task)
