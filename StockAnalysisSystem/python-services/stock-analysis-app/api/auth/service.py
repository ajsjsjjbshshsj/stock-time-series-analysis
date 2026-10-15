"""Opaque server-side sessions, Argon2id passwords and current-role decisions."""
import hashlib
import hmac
import re
import secrets
import time
from uuid import uuid4
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError,VerificationError,InvalidHashError
from sqlalchemy import select,delete,func
from sqlalchemy.exc import IntegrityError
from .repository import users,sessions,limits,audit


class RateLimited(PermissionError): pass


def digest(value): return hashlib.sha256(value.encode()).hexdigest()


def username(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{3,64}',value):
        raise ValueError('Invalid account name')
    return value.lower()


def password(value):
    if not isinstance(value,str) or not 12<=len(value)<=128 or len(value.encode())>1024:
        raise ValueError('Password must contain 12 to 128 characters')
    return value


def account(row):
    return {k:row[k] for k in ('id','username','role','enabled','must_change_password')}


class AuthService:
    def __init__(self,repository,*,secret,clock=None):
        if not isinstance(secret,str) or len(secret.encode())<32: raise ValueError('Auth secret required')
        self.repo=repository; self.secret=secret.encode(); self.clock=clock or time.time
        self.hasher=PasswordHasher(time_cost=3,memory_cost=65536,parallelism=2)
        self.dummy=self.hasher.hash(secrets.token_urlsafe(32))

    def _verify(self,hashed,value):
        if not isinstance(value,str) or len(value.encode())>1024: return False
        try: return self.hasher.verify(hashed,value)
        except (VerifyMismatchError,VerificationError,InvalidHashError): return False

    def _audit(self,c,action,actor=None,target=None):
        c.execute(audit.insert().values(id=uuid4().hex,actor_id=actor,target_id=target,
            action=action,created_at=int(self.clock())))

    def _admin(self,c,actor):
        row=c.execute(select(users).where(users.c.id==actor)).mappings().one_or_none()
        if row is None or not row['enabled'] or row['role']!='admin' or row['must_change_password']:
            raise PermissionError('Access denied')
        return row

    def _new_user(self,c,name,value,role,force):
        if role not in ('user','admin'): raise ValueError('Invalid account role')
        body=dict(id=uuid4().hex,username=username(name),password_hash=self.hasher.hash(password(value)),
                  role=role,enabled=True,must_change_password=force)
        c.execute(users.insert().values(**body))
        return account(body)

    def bootstrap(self,name,value):
        with self.repo.transaction() as c:
            if c.execute(select(func.count()).select_from(users)).scalar_one():
                raise ValueError('Bootstrap already completed')
            result=self._new_user(c,name,value,'admin',False)
            self._audit(c,'BOOTSTRAP',target=result['id'])
            return result

    def create_user(self,actor,name,value,role='user'):
        try:
            with self.repo.transaction() as c:
                self._admin(c,actor)
                result=self._new_user(c,name,value,role,True)
                self._audit(c,'CREATE_USER',actor,result['id'])
                return result
        except IntegrityError:
            raise ValueError('Account already exists') from None

    def list_users(self,actor):
        with self.repo.transaction() as c:
            self._admin(c,actor)
            return [account(r) for r in c.execute(select(users).order_by(users.c.username)).mappings()]

    def update_user(self,actor,target,*,role=None,enabled=None,new_password=None):
        if role is not None and role not in ('user','admin'): raise ValueError('Invalid account role')
        if enabled is not None and type(enabled) is not bool: raise ValueError('Invalid enabled flag')
        with self.repo.transaction() as c:
            self._admin(c,actor)
            row=c.execute(select(users).where(users.c.id==target)).mappings().one_or_none()
            if row is None: raise ValueError('Account unavailable')
            updated=dict(row)
            if role is not None: updated['role']=role
            if enabled is not None: updated['enabled']=enabled
            if row['role']=='admin' and row['enabled'] and (updated['role']!='admin' or not updated['enabled']):
                count=c.execute(select(func.count()).select_from(users).where(users.c.role=='admin',users.c.enabled==True)).scalar_one()
                if count<=1: raise ValueError('Last administrator must remain enabled')
            if new_password is not None:
                updated.update(password_hash=self.hasher.hash(password(new_password)),must_change_password=True)
            c.execute(users.update().where(users.c.id==target).values(**{k:v for k,v in updated.items() if k!='id'}))
            c.execute(delete(sessions).where(sessions.c.user_id==target))
            self._audit(c,'UPDATE_USER',actor,target)
            return account(updated)

    def _consume_limit(self,keys,maximum):
        now=int(self.clock())
        reservations={}
        with self.repo.transaction() as c:
            rows={key:c.execute(select(limits).where(limits.c.key_hash==key)).mappings().one_or_none() for key in keys}
            if any(r is not None and now-r['window_start']<900 and r['attempts']>=maximum for r in rows.values()):
                raise RateLimited('Too many attempts')
            for key,row in rows.items():
                body=dict(window_start=now,attempts=1)
                if row is None: c.execute(limits.insert().values(key_hash=key,**body))
                else:
                    if now-row['window_start']<900: body=dict(window_start=row['window_start'],attempts=row['attempts']+1)
                    c.execute(limits.update().where(limits.c.key_hash==key).values(**body))
                reservations[key]=body['window_start']
        return reservations

    def _new_session(self,c,user_id=None):
        token=secrets.token_urlsafe(32); now=int(self.clock())
        c.execute(sessions.insert().values(token_hash=digest(token),user_id=user_id,
            created_at=now,expires_at=now+(28800 if user_id else 900),last_seen=now))
        return token

    def _active(self,c,token):
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}',token): return None
        row=c.execute(select(sessions).where(sessions.c.token_hash==digest(token))).mappings().one_or_none()
        now=int(self.clock())
        if row is None or row['expires_at']<=now or now-row['last_seen']>1800: return None
        user=None
        if row['user_id']:
            user=c.execute(select(users).where(users.c.id==row['user_id'])).mappings().one_or_none()
            if user is None or not user['enabled']: return None
        c.execute(sessions.update().where(sessions.c.token_hash==digest(token)).values(last_seen=now))
        return dict(user=account(user) if user else None)

    def csrf(self,token): return hmac.new(self.secret,token.encode(),hashlib.sha256).hexdigest()

    def validate_csrf(self,token,value):
        if not isinstance(token,str) or not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{64}',value): return False
        with self.repo.transaction() as c:
            return self._active(c,token) is not None and hmac.compare_digest(self.csrf(token),value)

    def context(self,token,ip):
        with self.repo.transaction() as c: current=self._active(c,token)
        if current is None:
            self._consume_limit([digest('context:'+ip)],60)
            with self.repo.transaction() as c: token=self._new_session(c)
            current=dict(user=None)
        return dict(token=token,csrf=self.csrf(token),account=current['user'])

    def session(self,token):
        with self.repo.transaction() as c:
            current=self._active(c,token)
            return current['user'] if current else None

    def login(self,name,value,ip,previous_token=None):
        name=username(name)
        keys=[digest('login:name:'+name),digest('login:ip:'+ip)]
        reservations=self._consume_limit(keys,5)
        with self.repo.transaction() as c: user=c.execute(select(users).where(users.c.username==name)).mappings().one_or_none()
        valid=self._verify(user['password_hash'] if user else self.dummy,value)
        if not valid or user is None or not user['enabled']:
            with self.repo.transaction() as c: self._audit(c,'LOGIN_FAILED',target=user['id'] if user else None)
            raise PermissionError('Invalid credentials')
        with self.repo.transaction() as c:
            current=c.execute(select(users).where(users.c.id==user['id'])).mappings().one()
            if not current['enabled'] or current['password_hash']!=user['password_hash']: raise PermissionError('Invalid credentials')
            if previous_token: c.execute(delete(sessions).where(sessions.c.token_hash==digest(previous_token)))
            token=self._new_session(c,current['id'])
            # Release only this successful attempt, never other users' failures.
            # A slow password verification may finish after a new window begins.
            for key,window_start in reservations.items():
                c.execute(limits.update().where(limits.c.key_hash==key,
                    limits.c.window_start==window_start,limits.c.attempts>0)
                    .values(attempts=limits.c.attempts-1))
            self._audit(c,'LOGIN',current['id'],current['id'])
            return dict(token=token,csrf=self.csrf(token),account=account(current))

    def logout(self,token):
        with self.repo.transaction() as c:
            current=self._active(c,token)
            if current is not None:
                c.execute(delete(sessions).where(sessions.c.token_hash==digest(token)))
                self._audit(c,'LOGOUT',current['user']['id'] if current['user'] else None)

    def change_password(self,token,old_password,new_password):
        password(new_password)
        with self.repo.transaction() as c:
            current=self._active(c,token)
            if not current or not current['user']: raise PermissionError('Access denied')
            user=c.execute(select(users).where(users.c.id==current['user']['id'])).mappings().one()
            if not self._verify(user['password_hash'],old_password): raise PermissionError('Access denied')
            if old_password==new_password: raise ValueError('New password must differ from current password')
            c.execute(users.update().where(users.c.id==user['id']).values(
                password_hash=self.hasher.hash(new_password),must_change_password=False))
            c.execute(delete(sessions).where(sessions.c.user_id==user['id']))
            self._audit(c,'CHANGE_PASSWORD',user['id'],user['id'])
