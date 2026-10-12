from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pytest
from preparation_store_support import database, load, pool, request, NOW, LATER, OWNER


def setup(factory):
    pools = load('pool_repository', 'PoolRepository')(factory)
    saved = pools.register(pool(), activate=True)
    return load('preparation_repository', 'PreparationRepository')(factory), pools, request(saved['pool_version'])


def test_duplicate_active_submission_reuses_task_but_success_is_not_permanent_cache():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        first = repo.submit(req, now=NOW)
        assert repo.submit(req, now=NOW)['task_id'] == first['task_id']
        claimed = repo.claim(OWNER, now=NOW, lease_until=LATER)
        assert repo.update(claimed['task_id'], OWNER['token'], dict(status='SUCCEEDED',
            result=dict(data_ready=True), updated_at=NOW))
        assert repo.submit(req, now=NOW)['task_id'] != first['task_id']


def test_claim_and_fencing_prevent_wrong_owner_from_publishing():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        task = repo.submit(req, now=NOW)
        claimed = repo.claim(OWNER, now=NOW, lease_until=LATER)
        assert claimed['task_id'] == task['task_id']
        assert repo.claim(dict(OWNER, token='2'*32), now=NOW, lease_until=LATER) is None
        assert repo.update(task['task_id'], '2'*32, dict(phase='IMPORT', updated_at=NOW)) is False
        assert repo.heartbeat(task['task_id'], OWNER['token'], now=NOW, lease_until=LATER)
        assert repo.get(task['task_id'])['phase'] == 'QUEUED'


def test_new_active_pool_does_not_change_task_request():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, pools, req = setup(factory)
        task = repo.submit(req, now=NOW)
        pools.register(pool('600000.SH'), activate=True)
        assert repo.get(task['task_id'])['request']['pool_version'] == req['pool_version']


def test_expired_owner_cannot_extend_or_finish_without_explicit_recovery():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        task = repo.submit(req, now=NOW)
        repo.claim(OWNER, now=NOW, lease_until=LATER)
        assert repo.heartbeat(task['task_id'], OWNER['token'], now=LATER,
                              lease_until='2026-10-11T09:04:00Z') is False
        assert repo.update(task['task_id'], OWNER['token'], dict(status='SUCCEEDED',
            result=dict(data_ready=True), updated_at=LATER)) is False
        assert repo.get(task['task_id'])['status'] == 'RUNNING'


def test_quota_wait_not_claimed_before_deadline():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        task = repo.submit(req, now=NOW)
        repo.claim(OWNER, now=NOW, lease_until=LATER)
        assert repo.update(task['task_id'], OWNER['token'], dict(status='WAITING_RETRY',
            retry_not_before=LATER, error_kind='MINUTE_QUOTA', updated_at=NOW))
        assert repo.claim(OWNER, now=NOW, lease_until=LATER) is None
        assert repo.claim(dict(OWNER, token='2'*32), now=LATER,
                          lease_until='2026-10-11T09:04:00Z')['task_id'] == task['task_id']


def test_retry_keeps_identity_and_requires_terminal_failure():
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        task = repo.submit(req, now=NOW)
        with pytest.raises(ValueError): repo.retry(task['task_id'], now=NOW)
        repo.claim(OWNER, now=NOW, lease_until=LATER)
        repo.update(task['task_id'], OWNER['token'], dict(status='FAILED', error_kind='NETWORK', updated_at=NOW))
        retry = repo.retry(task['task_id'], now=LATER)
        assert retry['task_id'] == task['task_id']
        assert retry['status'] == 'QUEUED'
        assert retry['identity'] == task['identity']


@pytest.mark.parametrize('patch', [dict(status='UNKNOWN'), dict(error_kind='Token secret'),
    dict(request=dict(pool_version='other')), dict(owner_token='2'*32), dict(phase='../escape')])
def test_patch_cannot_mutate_request_or_store_raw_error(patch):
    load('preparation_repository', 'PreparationRepository')
    with database() as factory:
        repo, _, req = setup(factory)
        task = repo.submit(req, now=NOW)
        repo.claim(OWNER, now=NOW, lease_until=LATER)
        with pytest.raises(ValueError):
            repo.update(task['task_id'], OWNER['token'], dict(patch, updated_at=NOW))
