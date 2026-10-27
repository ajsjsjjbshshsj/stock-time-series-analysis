"""Administrator-owned opt-in saved-model plans. No training or publication methods."""
import time
from sqlalchemy import select
from api.auth.repository import users
from tasks.contracts import digest
from workflows.source import WorkflowSource
from .repository import DailyRepository


class DailyService:
    def __init__(self,engine,source=None):
        self.engine=engine;self.repo=DailyRepository(engine);self.source=source or WorkflowSource(engine)

    def admin(self,actor):
        with self.engine.connect() as c:row=c.execute(select(users).where(users.c.id==actor)).mappings().one_or_none()
        if row is None or not row['enabled'] or row['role']!='admin' or row['must_change_password']:
            raise PermissionError('Current administrator required')

    def proof(self,model_id):
        row,meta=self.source.model(model_id)
        data=meta['binding']['data']
        return dict(algorithm=row['algorithm'],model_sha256=row['manifest_sha256'],
            pool_sha256=digest(dict(stock_codes=data['stock_codes'],pool_identity=data['pool_identity'],registry_pool=meta['binding'].get('registry_pool'))))

    def create(self,actor,model_id):
        self.admin(actor);proof=self.proof(model_id)
        return self.repo.create(actor,model_id,proof,int(time.time()))

    def enable(self,actor,id,enabled):
        self.admin(actor);plan=self.repo.get(id)
        if plan['actor_id']!=actor:raise PermissionError('Only plan owner may change a plan')
        if enabled and self.proof(plan['model_id'])!=plan['proof']:raise ValueError('Saved model or frozen pool changed')
        return self.repo.enable(id,enabled)

    def validate(self,plan):
        self.admin(plan['actor_id'])
        if self.proof(plan['model_id'])!=plan['proof']:raise ValueError('Saved model or frozen pool changed')
