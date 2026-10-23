"""Cache or queue bounded saved-model predictions; never native inference in HTTP."""
import json,time,re
from contextlib import nullcontext
from uuid import uuid4
from sqlalchemy import select,func
from tasks.contracts import digest,encoded,Conflict
from tasks.repository import tasks
from workflows.contracts import normalize_operation
from .contracts import normalize
from .repository import releases,audit,cache,grants,account,release_row,check_cache


class PredictionService:
    def __init__(self,tasks,*,source=None,pools=None,workflow=None,completed=None,clock=None):
        self.tasks=tasks;self.engine=tasks.repo.engine;self.clock=clock or time.time
        if source is None:
            from workflows.source import WorkflowSource
            source=WorkflowSource(self.engine)
        self.source=source
        if pools is None:
            from api.preparation import production_services
            _,pools,_=production_services(self.engine)
        self.pools=pools
        if workflow is None:
            from workflows.service import WorkflowService
            workflow=WorkflowService(tasks,source.store)
        self.workflow=workflow
        self.completed=completed or self._completed

    def _completed(self):
        from tasks.source import read_calendar
        from workflows.source import completed_session
        return completed_session(read_calendar(self.source.calendar_path))

    def model_proof(self,id):
        row,meta=self.source.model(id);codes=meta['binding']['data']['stock_codes']
        if not codes or codes!=sorted(set(codes)) or len(codes)>10000 or any(re.fullmatch(r'[0-9]{6}\.(SH|SZ)',x) is None for x in codes):
            raise ValueError('Frozen model stock pool required')
        return dict(model_id=id,algorithm=row['algorithm'],manifest_sha256=row['manifest_sha256'],
            title=row['title'],stock_codes=codes,pool_identity=meta['binding']['data']['pool_identity'],score_units=meta['native'].get('score_units'))

    def release(self,actor,id,enabled):
        if type(enabled) is not bool:raise ValueError('Explicit enabled flag required')
        with self.engine.connect() as c:
            account(c,actor,admin=True)
            prior=release_row(c,id) if not enabled else None
        # Emergency withdrawal must not depend on a healthy model file.
        proof=prior['proof'] if prior is not None else self.model_proof(id)
        with self.tasks.repo.transaction() as (c,_):
            account(c,actor,admin=True,lock=True)
            old=c.execute(select(releases).where(releases.c.model_id==id)).mappings().one_or_none()
            if old and old['enabled']==enabled and old['sponsor_id']==actor and old['proof_sha256']==digest(proof):return dict(model_id=id,enabled=enabled)
            count=c.execute(select(func.count()).select_from(releases).where(releases.c.enabled==True)).scalar_one()
            if enabled and (old is None or not old['enabled']) and count>=20:raise Conflict('Enabled model limit reached')
            revision=uuid4().hex;now=int(self.clock());value=dict(sponsor_id=actor,revision=revision,enabled=enabled,proof=encoded(proof),proof_sha256=digest(proof),updated_at=now)
            if old:c.execute(releases.update().where(releases.c.model_id==id).values(**value))
            else:c.execute(releases.insert().values(model_id=id,**value))
            c.execute(audit.insert().values(id=uuid4().hex,actor_id=actor,model_id=id,revision=revision,enabled=enabled,created_at=now))
        return dict(model_id=id,enabled=enabled)

    def approved(self,c,id):
        row=release_row(c,id)
        if not row['enabled']:raise LookupError('Enabled model unavailable')
        account(c,row['sponsor_id'],admin=True)
        return row

    def selection(self,proof,version):
        if version=='model_'+proof['model_id']:
            codes=proof['stock_codes'];name='模型固定池 · '+str(len(codes))+'只'
        else:
            pool=self.pools.get(version);codes=pool['codes'];name=pool['display_name']
        if not codes or codes!=sorted(set(codes)) or not set(codes)<=set(proof['stock_codes']):raise ValueError('Model does not support the entire selected pool')
        return dict(pool_version=version,display_name=name,stock_codes=codes,stock_count=len(codes),member_sha256=digest(codes))

    def catalog(self,actor):
        with self.engine.connect() as c:
            account(c,actor);ids=c.execute(select(releases.c.model_id).where(releases.c.enabled==True).order_by(releases.c.model_id).limit(20)).scalars().all()
        versions=self.pools.versions(limit=50)['items'] if ids else [];models=[]
        for id in ids:
            with self.engine.connect() as c:release=self.approved(c,id)
            proof=self.model_proof(id)
            if proof!=release['proof']:raise ValueError('Approved model seal changed')
            options=[self.selection(proof,'model_'+id)]
            for pool in versions:
                try:options.append(self.selection(proof,pool['pool_version']))
                except ValueError:continue  # Unsupported complete pool, never intersect it.
            models.append(dict(id=id,title=proof['title'],algorithm=proof['algorithm'],model_sha256=proof['manifest_sha256'],
                inference_pool_count=len(proof['stock_codes']),pools=[{k:p[k] for k in ('pool_version','display_name','stock_count')} for p in options]))
        return dict(models=models,training_allowed=False,requires_result_publication=False)

    def admin_models(self,actor):
        with self.engine.connect() as c:
            account(c,actor,admin=True)
            # All enabled models (bounded20) precede historical disabled rows.
            return dict(items=[dict(r) for r in c.execute(select(releases.c.model_id,releases.c.enabled,releases.c.updated_at).order_by(releases.c.enabled.desc(),releases.c.updated_at.desc(),releases.c.model_id).limit(100)).mappings()])

    def install(self,c,task,actor,key,release,selection,request,binding,result_id=None):
        account(c,actor);current=self.approved(c,release['model_id'])
        if current['revision']!=release['revision']:raise Conflict('Model approval changed')
        if task is not None and (json.loads(task['request'])!=request or digest(json.loads(task['binding']))!=digest(binding)):
            raise Conflict('Prediction inputs changed during enqueue')
        old=c.execute(select(cache).where(cache.c.id==key)).mappings().one_or_none()
        if old is None:
            if task is not None:
                active=cache.join(tasks,cache.c.task_id==tasks.c.id)
                pending=tasks.c.state.in_(['QUEUED','RUNNING','CANCEL_REQUESTED'])
                global_count=c.execute(select(func.count()).select_from(active).where(pending)).scalar_one()
                own=c.execute(select(func.count()).select_from(active.join(grants,grants.c.prediction_id==cache.c.id)).where(pending,grants.c.user_id==actor)).scalar_one()
                if global_count>=20 or own>=2:raise Conflict('Prediction queue quota reached')
            c.execute(cache.insert().values(id=key,model_id=release['model_id'],revision=release['revision'],sponsor_id=release['sponsor_id'],
                selection=encoded(selection),binding_sha256=digest(binding),request=encoded(request),task_id=task['id'] if task else None,result_id=result_id,created_at=int(self.clock())))
        elif (old['binding_sha256']!=digest(binding) or old['selection']!=encoded(selection) or old['request']!=encoded(request)
                or old['revision']!=release['revision'] or old['task_id']!=(task['id'] if task else None) or old['result_id']!=result_id):
            raise Conflict('Prediction cache identity changed')
        if c.execute(select(grants).where(grants.c.prediction_id==key,grants.c.user_id==actor)).first() is None:
            if old is not None and task is not None and task['state'] in ('QUEUED','RUNNING','CANCEL_REQUESTED'):
                joined=cache.join(tasks,cache.c.task_id==tasks.c.id).join(grants,grants.c.prediction_id==cache.c.id)
                own=c.execute(select(func.count()).select_from(joined).where(grants.c.user_id==actor,tasks.c.state.in_(['QUEUED','RUNNING','CANCEL_REQUESTED']))).scalar_one()
                if own>=2:raise Conflict('User prediction queue quota reached')
            c.execute(grants.insert().values(prediction_id=key,user_id=actor,created_at=int(self.clock())))

    def precomputed(self,model,request,binding,connection=None):
        from workflows.repository import results
        from tasks.artifacts import read_json
        with (self.engine.connect() if connection is None else nullcontext(connection)) as c:
            rows=c.execute(select(results).where(results.c.model_id==model).order_by(results.c.seq.desc()).limit(20)).mappings().all()
        for raw in rows:
            row=dict(raw);row['metadata']=json.loads(row['metadata'])
            if row['metadata'].get('operation')!='predict' or row['metadata'].get('status')!='COMPLETE':continue
            self.workflow._verified(row,'result')
            meta=read_json(self.workflow.store.path(row['id'])/'metadata.json',max_bytes=8*1024*1024)
            old=meta['request']
            if digest(meta['binding'])==digest(binding) and all(old.get(k)==request.get(k) for k in ('model_id','algorithm','start_date','end_date','operation')):return row['id']
        return None

    def pending(self,c,request,binding,sponsor,exclude=None):
        linked=select(cache.c.id).where(cache.c.task_id==tasks.c.id).exists()
        query=select(tasks).where(tasks.c.actor_id==sponsor,tasks.c.request_hash==digest(request),
            tasks.c.state.in_(['QUEUED','RUNNING']),~linked).order_by(tasks.c.seq).limit(20)
        if exclude is not None:query=query.where(tasks.c.id!=exclude)
        sha=digest(binding)
        for row in c.execute(query).mappings():
            if json.loads(row['request'])==request and digest(json.loads(row['binding']))==sha:return row
        return None

    def submit(self,actor,value):
        value=normalize(value)
        with self.engine.connect() as c:account(c,actor);release=self.approved(c,value['model_id'])
        proof=self.model_proof(value['model_id'])
        if proof!=release['proof']:raise ValueError('Approved model seal changed')
        selection=self.selection(proof,value['pool_version'])
        active=self.tasks.source.describe();day=min(active['source_cutoff'],self.completed())
        request=normalize_operation(dict(kind='model_workflow',operation='predict',algorithm=proof['algorithm'],model_id=value['model_id'],
            start_date=day,end_date=day,feature_build_id=active['build_id']))
        binding=self.source.bind(request)
        key=digest(dict(revision=release['revision'],model_sha256=proof['manifest_sha256'],selection=selection,request=request,binding_sha256=digest(binding)))
        with self.engine.connect() as c:old=c.execute(select(cache).where(cache.c.id==key)).mappings().one_or_none()
        if old:
            with self.tasks.repo.transaction() as (c,_):
                task=c.execute(select(tasks).where(tasks.c.id==old['task_id'])).mappings().one() if old['task_id'] else None
                self.install(c,task,actor,key,release,selection,request,binding,old['result_id'])
        else:
            existing=self.precomputed(value['model_id'],request,binding)
            if existing:
                with self.tasks.repo.transaction() as (c,_):self.install(c,None,actor,key,release,selection,request,binding,existing)
            else:
                with self.tasks.repo.transaction() as (c,_):
                    pending=self.pending(c,request,binding,release['sponsor_id'])
                    if pending is not None:self.install(c,pending,actor,key,release,selection,request,binding)
                if pending is None:
                    def install_new(c,task):
                        # Another nightly task can arrive while source binding
                        # runs. Roll back this new task rather than duplicate it;
                        # an explicit retry will join its task or sealed result.
                        if c.execute(select(cache.c.id).where(cache.c.id==key)).first() is None:
                            if self.pending(c,request,binding,release['sponsor_id'],exclude=task['id']) is not None or self.precomputed(value['model_id'],request,binding,c):
                                raise Conflict('Matching prediction appeared during enqueue; retry to reuse it')
                        self.install(c,task,actor,key,release,selection,request,binding)
                    self.tasks.create(release['sponsor_id'],request,key[:32],on_create=install_new)
        return self.get(actor,key)

    def authorized(self,actor,key):
        if not isinstance(key,str) or re.fullmatch('[0-9a-f]{64}',key) is None:raise LookupError('Prediction unavailable')
        with self.engine.connect() as c:
            account(c,actor)
            row=c.execute(select(cache).join(grants,grants.c.prediction_id==cache.c.id).where(cache.c.id==key,grants.c.user_id==actor)).mappings().one_or_none()
            if row is None:raise LookupError('Prediction unavailable')
            release=self.approved(c,row['model_id'])
            if row['revision']!=release['revision']:raise LookupError('Prediction approval revoked')
            check_cache(row,release)
        if self.model_proof(row['model_id'])!=release['proof']:raise ValueError('Approved model changed')
        return dict(row),release

    def get(self,actor,key):
        row,release=self.authorized(actor,key);request=json.loads(row['request']);selection=json.loads(row['selection'])
        state='SUCCEEDED' if row['result_id'] else self.tasks.get(row['task_id'])['state']
        result_id=row['result_id'] or (row['task_id'] if state=='SUCCEEDED' else None)
        if result_id is not None:
            result=self.workflow.repo.get_result(result_id)
            if result is None or result['metadata'].get('status')!='COMPLETE' or result['metadata'].get('signal_end')!=request['end_date']:
                state='NOT_READY';result_id=None
        return dict(id=key,task_id=row['task_id'],state=state,result_ready=result_id is not None,model_id=row['model_id'],
            algorithm=release['proof']['algorithm'],signal_date=request['end_date'],selection={k:v for k,v in selection.items() if k!='stock_codes'},
            inference_pool_count=len(release['proof']['stock_codes']),training_allowed=False)

    def result(self,actor,key,*,limit=50,after=0):
        row,release=self.authorized(actor,key);status=self.get(actor,key)
        if not status['result_ready']:raise Conflict('Prediction result not ready')
        id=row['result_id'] or row['task_id'];selected=json.loads(row['selection'])
        value=self.workflow.result(id,public=False,section='scores',limit=limit,after=after,stock_codes=selected['stock_codes'])
        from tasks.artifacts import read_json
        meta=read_json(self.workflow.store.path(id)/'metadata.json',max_bytes=8*1024*1024)
        if digest(meta['binding'])!=row['binding_sha256'] or meta['request'].get('model_id')!=row['model_id'] or meta['request'].get('operation')!='predict':
            raise ValueError('Cached result inputs differ')
        if value['report'].get('status')!='COMPLETE' or value['report'].get('signal_end')!=status['signal_date']:
            raise Conflict('Requested date has no complete prediction')
        if any(r.get('ts_code') not in selected['stock_codes'] for r in value['rows']):raise ValueError('Prediction selection escaped')
        return dict(prediction=status,report=value['report'],rows=value['rows'],next_cursor=value['next_cursor'],view='filtered_frozen_model_pool',requires_publication=False)
