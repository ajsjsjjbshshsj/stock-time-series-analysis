"""Explicit auth schema and transaction boundary, separate from market tables."""
from contextlib import contextmanager
from sqlalchemy import (MetaData,Table,Column,String,Integer,BigInteger,Boolean,ForeignKey,CheckConstraint,select)

schema=MetaData()
control=Table('analysis_auth_control',schema,Column('id',Integer,primary_key=True))
users=Table('analysis_auth_user',schema,
    Column('id',String(32),primary_key=True),Column('username',String(64),nullable=False,unique=True),
    Column('password_hash',String(512),nullable=False),Column('role',String(8),nullable=False),
    Column('enabled',Boolean,nullable=False),Column('must_change_password',Boolean,nullable=False),
    CheckConstraint("role IN ('user','admin')",name='ck_analysis_auth_role'))
sessions=Table('analysis_auth_session',schema,
    Column('token_hash',String(64),primary_key=True),
    Column('user_id',String(32),ForeignKey('analysis_auth_user.id',ondelete='CASCADE'),index=True),
    Column('created_at',BigInteger,nullable=False),Column('expires_at',BigInteger,nullable=False),
    Column('last_seen',BigInteger,nullable=False))
limits=Table('analysis_auth_limit',schema,Column('key_hash',String(64),primary_key=True),
    Column('window_start',BigInteger,nullable=False),Column('attempts',Integer,nullable=False))
audit=Table('analysis_auth_audit',schema,Column('id',String(32),primary_key=True),
    Column('actor_id',String(32)),Column('target_id',String(32)),Column('action',String(32),nullable=False),
    Column('created_at',BigInteger,nullable=False))


class AuthRepository:
    def __init__(self,engine): self.engine=engine

    def migrate(self):
        """Operator-only. No application startup or request invokes this."""
        schema.create_all(self.engine)
        with self.engine.begin() as connection:
            if connection.execute(select(control.c.id).where(control.c.id==1)).first() is None:
                connection.execute(control.insert().values(id=1))

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            # All auth writes take this row first: protects the last admin and
            # durable limiter counters across processes, not merely threads.
            if connection.execute(select(control.c.id).where(control.c.id==1).with_for_update()).first() is None:
                raise RuntimeError('Auth schema requires explicit migration')
            yield connection
