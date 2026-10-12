from copy import deepcopy
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from sqlalchemy import text
from nightly_store_support import database,store,submit,claim,NOW,UNTIL,OWNER
from nightly_support import reference
from app.nightly.contracts import freeze_market

def snapshot(day='2026-09-28'):
    return freeze_market(day,[reference()],'a'*64,'2026-10-11T20:00:00+08:00')


def test_gap_version_parser_accepts_only_bounded_full_binding_prefix():
    from app.nightly.contracts import schema_for_version
    from app.repositories.market_update_repository import MarketUpdateRepository
    assert schema_for_version('market-gap-v1:'+'a'*16)==2
    assert schema_for_version('market-v1')==1 and schema_for_version('market-v2')==2
    with database() as factory:
        for invalid in ('market-gap-v1','market-gap-v1:'+'a'*64,'market-gap-v1:'+'g'*16):
            with pytest.raises(ValueError,match='VERSION'):
                MarketUpdateRepository(factory,requirements_version=invalid)

def test_policy_upgrade_preserves_old_tasks_and_isolates_new_queue():
    from app.repositories.market_update_repository import MarketUpdateRepository
    with database() as factory:
        old=store(factory); first=submit(old)
        new=MarketUpdateRepository(factory,requirements_version='market-v2')
        second=submit(new)
        assert first['task_id']!=second['task_id']
        assert new.get(first['task_id'])==first
        assert claim(new)['task_id']==second['task_id']
        value=freeze_market('2026-09-28',[reference()],'a'*64,'2026-10-11T20:00:00+08:00',schema_version=2)
        assert new.bind_universe(second['task_id'],OWNER['token'],value,now=NOW)['universe']==value
        assert old.get(first['task_id'])==first

def test_new_job_cannot_bind_old_policy_snapshot():
    from app.repositories.market_update_repository import MarketUpdateRepository
    with database() as factory:
        repo=MarketUpdateRepository(factory,requirements_version='market-v2')
        submit(repo); task=claim(repo)
        with pytest.raises(ValueError,match='VERSION'):repo.bind_universe(task['task_id'],OWNER['token'],snapshot(),now=NOW)

def test_cross_version_retry_rejected_without_mutating_legacy_task():
    from app.repositories.market_update_repository import MarketUpdateRepository
    with database() as factory:
        old=store(factory);submit(old);task=claim(old)
        old.update(task['task_id'],OWNER['token'],dict(status='FAILED',error_kind='SOURCE_CONFLICT',updated_at=NOW))
        before=old.get(task['task_id'])
        new=MarketUpdateRepository(factory,requirements_version='market-v2')
        with pytest.raises(ValueError,match='VERSION'):new.retry(task['task_id'],now=NOW)
        assert new.get(task['task_id'])==before

def test_duplicate_submission_preserves_identity_and_original_schedule():
    with database() as factory:
        repo=store(factory);first=submit(repo)
        second=repo.submit('2026-09-28',scheduled_for='2026-09-28T21:00:00+08:00',now='2026-09-28T13:00:00Z')
        assert first['task_id']==second['task_id']
        assert second['request']['scheduled_for']=='2026-09-28T20:00:00+08:00'
        assert second['status']=='QUEUED' and second['universe'] is None

@pytest.mark.parametrize('day,scheduled,now',[
    ('2022-12-30','2022-12-30T20:00:00+08:00',NOW),
    ('2026-09-28','2026-09-28T20:00:00+08:00','2026-09-28T11:59:59Z'),
    ('2026-09-28','2026-09-29T20:00:00+08:00','2026-09-29T12:00:00Z'),
    ('2026-09-28','2026-09-28T16:00:00+08:00',NOW),
    ('2026-09-28','2026-09-28T20:00:01+08:00',NOW),
    ('2026-09-28','2026-09-28T20:00:00',NOW)])
def test_invalid_or_premature_submissions_do_not_create_task(day,scheduled,now):
    with database() as factory:
        repo=store(factory)
        with pytest.raises(ValueError):repo.submit(day,scheduled_for=scheduled,now=now)
        assert repo.earliest_date() is None

def test_claim_oldest_market_day_and_fence_ownership():
    with database() as factory:
        repo=store(factory);submit(repo,'2026-09-29','2026-09-29T12:00:00Z')
        first=submit(repo,'2026-09-28','2026-09-29T12:00:00Z')
        claimed=repo.claim(OWNER,now='2026-09-29T12:00:00Z',lease_until='2026-09-29T12:02:00Z')
        assert claimed['task_id']==first['task_id'] and claimed['status']=='RUNNING'
        assert repo.fence(first['task_id'],OWNER['token'],now='2026-09-29T12:00:01Z')['task_id']==first['task_id']
        with pytest.raises(RuntimeError,match='OWNER'):repo.fence(first['task_id'],'2'*32,now='2026-09-29T12:00:01Z')

def test_snapshot_binds_once_and_cannot_switch_members():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo);value=snapshot()
        bound=repo.bind_universe(task['task_id'],OWNER['token'],value,now=NOW)
        assert bound['universe']['codes']==['000001.SZ']
        assert repo.bind_universe(task['task_id'],OWNER['token'],value,now=NOW)['universe']==value
        different=freeze_market('2026-09-28',[reference('600000.SH',exchange='SSE')],'b'*64,'2026-10-11T20:00:00+08:00')
        with pytest.raises(ValueError):repo.bind_universe(task['task_id'],OWNER['token'],different,now=NOW)
        assert repo.get(task['task_id'])['universe']==value

def test_other_date_snapshot_or_expired_owner_cannot_bind():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        with pytest.raises(ValueError):repo.bind_universe(task['task_id'],OWNER['token'],snapshot('2026-09-29'),now=NOW)
        with pytest.raises(RuntimeError,match='OWNER'):repo.bind_universe(task['task_id'],OWNER['token'],snapshot(),now=UNTIL)

def test_terminal_runs_are_not_recreated_by_scheduler():
    with database() as factory:
        repo=store(factory);original=submit(repo);task=claim(repo)
        assert repo.update(task['task_id'],OWNER['token'],dict(status='PARTIAL',result=dict(data_ready=False),updated_at=NOW))
        repeated=submit(repo,now='2026-09-29T12:00:00Z')
        assert repeated['task_id']==original['task_id'] and repeated['status']=='PARTIAL'
        assert repeated['owner_token'] is None and repeated['lease_until'] is None

def test_manual_retry_increments_attempt_but_preserves_snapshot():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        repo.bind_universe(task['task_id'],OWNER['token'],snapshot(),now=NOW)
        assert repo.update(task['task_id'],OWNER['token'],dict(status='FAILED',error_kind='NETWORK',updated_at=NOW))
        retried=repo.retry(task['task_id'],now='2026-09-28T12:00:01Z')
        assert retried['attempt_no']==2 and retried['status']=='QUEUED'
        assert retried['universe']['codes']==['000001.SZ']
        with pytest.raises(ValueError):repo.retry(task['task_id'],now='2026-09-28T12:00:02Z')

def test_cooldown_stays_unclaimable_until_future_deadline():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        assert repo.update(task['task_id'],OWNER['token'],dict(status='WAITING_RETRY',retry_not_before='2026-09-28T13:00:00Z',error_kind='HOURLY_QUOTA',updated_at=NOW))
        assert repo.claim(OWNER,now='2026-09-28T12:59:59Z',lease_until='2026-09-28T13:01:59Z') is None
        assert repo.next_retry()=='2026-09-28T13:00:00+00:00'
        ready=repo.claim(OWNER,now='2026-09-28T13:00:00Z',lease_until='2026-09-28T13:02:00Z')
        assert ready['task_id']==task['task_id'] and ready['attempt_no']==1

def test_expired_retry_deadline_and_unknown_state_cannot_be_persisted():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        for patch in (dict(status='WAITING_RETRY',retry_not_before=NOW),dict(status='SUCCESS'),dict(phase='DELETE'),dict(universe_json='{}')):
            with pytest.raises(ValueError):repo.update(task['task_id'],OWNER['token'],dict(patch,updated_at=NOW))
        assert repo.get(task['task_id'])['status']=='RUNNING'

def test_expired_owner_cannot_heartbeat_or_publish_and_recovery_fences_old_token():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        assert not repo.heartbeat(task['task_id'],OWNER['token'],now=UNTIL,lease_until='2026-09-28T12:04:00Z')
        assert not repo.update(task['task_id'],OWNER['token'],dict(status='SUCCEEDED',updated_at=UNTIL))
        assert len(repo.expired(now=UNTIL))==1
        assert repo.recover(task['task_id'],OWNER['token'],now=UNTIL)
        new=dict(OWNER,token='2'*32)
        repo.claim(new,now=UNTIL,lease_until='2026-09-28T12:04:00Z')
        assert not repo.update(task['task_id'],OWNER['token'],dict(status='FAILED',updated_at=UNTIL))

def test_summary_scan_and_earliest_do_not_load_universe_json():
    with database() as factory:
        repo=store(factory);submit(repo);task=claim(repo)
        with factory.begin() as session:session.execute(text("UPDATE market_update_run SET universe_json='bad' WHERE task_id=:id"),dict(id=task['task_id']))
        rows=repo.list_runs('2026-09-28','2026-09-29')
        assert rows[0]['trade_date']=='2026-09-28' and 'universe' not in rows[0]
        assert repo.earliest_date()=='2026-09-28'
        with pytest.raises(ValueError):repo.get(task['task_id'])

def test_request_or_universe_tamper_not_trusted():
    with database() as factory:
        repo=store(factory);task=submit(repo)
        with factory.begin() as session:session.execute(text("UPDATE market_update_run SET request_json='{}' WHERE task_id=:id"),dict(id=task['task_id']))
        with pytest.raises(ValueError):repo.get(task['task_id'])

def test_bad_task_id_is_rejected_before_lookup():
    with database() as factory:
        with pytest.raises(ValueError):store(factory).get('../bad')

def test_duplicate_key_race_uses_real_constraint_then_rereads_same_run(monkeypatch):
    with database() as factory:
        repo=store(factory);original=submit(repo);execute=factory.class_.execute;miss=[True]
        def raced(session,statement,*args,**kwargs):
            if miss[0] and str(statement).startswith('SELECT task_id FROM market_update_run WHERE identity='):
                miss[0]=False
                return execute(session,text('SELECT task_id FROM market_update_run WHERE 1=0'))
            return execute(session,statement,*args,**kwargs)
        monkeypatch.setattr(factory.class_,'execute',raced)
        repeated=submit(repo)
        assert repeated['task_id']==original['task_id']
        assert len(repo.list_runs('2026-09-28','2026-09-29'))==1

def test_nonduplicate_integrity_error_is_safe_and_rolls_back(monkeypatch):
    from sqlalchemy.exc import IntegrityError
    with database() as factory:
        repo=store(factory);execute=factory.class_.execute
        def failure(session,statement,*args,**kwargs):
            if str(statement).startswith('INSERT INTO market_update_run '):
                raise IntegrityError('secret-sql',{'password':'private'},Exception(9999,'secret-password'))
            return execute(session,statement,*args,**kwargs)
        monkeypatch.setattr(factory.class_,'execute',failure)
        with pytest.raises(RuntimeError,match='^STORAGE_UNAVAILABLE$') as caught:submit(repo)
        assert caught.value.__context__ is None
        assert repo.earliest_date() is None

def test_other_scope_does_not_pollute_start_summary_or_queue():
    with database() as factory:
        repo=store(factory);task=submit(repo)
        with factory.begin() as session:
            session.execute(text("UPDATE market_update_run SET market_scope='OTHER' WHERE task_id=:id"),dict(id=task['task_id']))
        assert repo.earliest_date() is None
        assert repo.list_runs('2026-09-28','2026-09-29')==[]
        assert repo.next_retry() is None
        assert repo.claim(OWNER,now=NOW,lease_until=UNTIL) is None
