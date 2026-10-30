"""Durable per-route delivery, not an unsafe max-source-ID cursor."""
from contextlib import contextmanager
import re
import time
from sqlalchemy import MetaData,Table,Column,String,Text,BigInteger,Integer,Boolean,Index,select,or_,and_
from sqlalchemy.dialects.mysql import LONGTEXT

metadata=MetaData()
outbox=Table('indicator_outbox',metadata,
    Column('id',BigInteger().with_variant(Integer,'sqlite'),primary_key=True,autoincrement=True),
    Column('ts_code',String(9),nullable=False),Column('trade_date',String(10),nullable=False),
    Column('operation',String(6),nullable=False),Column('payload',Text().with_variant(LONGTEXT(),'mysql'),nullable=False),
    Column('captured_at',BigInteger,nullable=False),mysql_engine='InnoDB')
routes=Table('indicator_outbox_route',metadata,
    Column('id',String(32),primary_key=True),Column('input_topic',String(249),nullable=False,unique=True),
    Column('enabled',Boolean,nullable=False),Column('created_at',BigInteger,nullable=False),mysql_engine='InnoDB')
deliveries=Table('indicator_outbox_delivery',metadata,
    Column('route_id',String(32),primary_key=True),Column('outbox_id',BigInteger,primary_key=True),
    Column('state',String(16),nullable=False),Column('owner',String(32)),Column('fence',BigInteger,nullable=False),
    Column('lease_until',BigInteger),Column('attempts',BigInteger,nullable=False),
    Column('broker_partition',Integer),Column('broker_offset',BigInteger),Column('acked_at',BigInteger),mysql_engine='InnoDB')
Index('ix_indicator_delivery_pending',deliveries.c.route_id,deliveries.c.state,deliveries.c.lease_until,deliveries.c.outbox_id)
Index('ix_indicator_outbox_stock_order',outbox.c.ts_code,outbox.c.id)
Index('ix_indicator_outbox_point_revision',outbox.c.ts_code,outbox.c.trade_date,outbox.c.id)


def identity(value):
    if type(value) is not str or re.fullmatch('[0-9a-f]{32}',value) is None:raise ValueError('Invalid indicator route/owner')
    return value


def integer(value,minimum,maximum):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('Invalid indicator bound')
    return value


def topic(value):
    if (type(value) is not str or len(value)>249 or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*',value) is None
            or not value.startswith('stock.indicators.') or not value.endswith('.raw.v2')):
        raise ValueError('Isolated indicator input topic required')
    return value


class Outbox:
    def __init__(self,factory,*,clock=None):self.factory=factory;self.clock=clock or time.time

    @contextmanager
    def transaction(self):
        with self.factory() as session:
            try:
                if session.get_bind().dialect.name=='sqlite':session.connection().exec_driver_sql('BEGIN IMMEDIATE')
                yield session
                session.commit()
            except Exception:
                session.rollback();raise

    @staticmethod
    def route(session,id):
        row=session.execute(select(routes).where(routes.c.id==identity(id)).with_for_update()).mappings().one_or_none()
        if row is None:raise ValueError('Indicator route not registered')
        return row

    def register(self,id,input_topic):
        identity(id);topic(input_topic)
        with self.transaction() as session:
            prior=session.execute(select(routes).where(routes.c.id==id)).mappings().one_or_none()
            if prior:
                if prior['input_topic']!=input_topic:raise ValueError('Indicator route binding changed')
                return
            session.execute(routes.insert().values(id=id,input_topic=input_topic,enabled=False,created_at=int(self.clock())))

    def enable(self,id,expected_topic):
        topic(expected_topic)
        with self.transaction() as session:
            row=self.route(session,id)
            if row['input_topic']!=expected_topic:raise ValueError('Indicator route binding changed')
            session.execute(routes.update().where(routes.c.id==id).values(enabled=True))

    def enroll(self,id,*,limit=1000):
        integer(limit,1,5000)
        with self.transaction() as session:
            self.route(session,id)
            linked=select(deliveries.c.outbox_id).where(deliveries.c.route_id==id,deliveries.c.outbox_id==outbox.c.id).exists()
            ids=session.execute(select(outbox.c.id).where(~linked).order_by(outbox.c.id).limit(limit)).scalars().all()
            if ids:session.execute(deliveries.insert(),[dict(route_id=id,outbox_id=n,state='PENDING',fence=0,attempts=0) for n in ids])
            return len(ids)

    def claim(self,id,owner,*,limit=100,lease_seconds=60):
        identity(owner);integer(limit,1,100);integer(lease_seconds,1,300)
        with self.transaction() as session:
            route=self.route(session,id)
            if not route['enabled']:return []
            now=int(self.clock())
            pending=or_(deliveries.c.state=='PENDING',and_(deliveries.c.state=='LEASED',deliveries.c.lease_until<=now))
            prior=deliveries.alias('prior_delivery');event=outbox.alias('prior_event')
            blocked=select(prior.c.outbox_id).select_from(prior.join(event,prior.c.outbox_id==event.c.id)).where(
                prior.c.route_id==id,prior.c.state!='ACKED',event.c.ts_code==outbox.c.ts_code,event.c.id<outbox.c.id).correlate(outbox).exists()
            rows=session.execute(select(outbox,deliveries.c.fence,deliveries.c.attempts).join(deliveries,deliveries.c.outbox_id==outbox.c.id)
                .where(deliveries.c.route_id==id,pending,~blocked).order_by(outbox.c.id).limit(limit).with_for_update()).mappings().all()
            result=[]
            for row in rows:
                # Row-lock waits can outlive the route-lock acquisition too.
                fence=row['fence']+1;until=int(self.clock())+lease_seconds
                session.execute(deliveries.update().where(deliveries.c.route_id==id,deliveries.c.outbox_id==row['id'])
                    .values(state='LEASED',owner=owner,fence=fence,lease_until=until,attempts=row['attempts']+1))
                result.append(dict(row,fence=fence,input_topic=route['input_topic'],lease_until=until))
            return result

    def ack(self,id,source_id,owner,fence,*,topic,partition,offset):
        identity(owner);integer(source_id,1,2**63-1);integer(fence,1,2**63-1)
        integer(partition,0,2**31-1);integer(offset,0,2**63-1)
        with self.transaction() as session:
            route=self.route(session,id)
            row=session.execute(select(deliveries).where(deliveries.c.route_id==id,deliveries.c.outbox_id==source_id).with_for_update()).mappings().one_or_none()
            now=int(self.clock())
            if (not route['enabled'] or route['input_topic']!=topic or row is None
                    or row['owner']!=owner or row['fence']!=fence):raise ValueError('Indicator delivery lease changed')
            if row['state']=='ACKED':
                if row['broker_partition']!=partition or row['broker_offset']!=offset:raise ValueError('Broker receipt changed')
                return
            if row['state']!='LEASED' or row['lease_until'] is None or row['lease_until']<=now:
                raise ValueError('Indicator delivery lease expired')
            session.execute(deliveries.update().where(deliveries.c.route_id==id,deliveries.c.outbox_id==source_id)
                .values(state='ACKED',lease_until=None,broker_partition=partition,broker_offset=offset,acked_at=now))
