"""Read-only frozen transformer signal HTTP boundary: discovery, integrity, fail-closed."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from tests.test_transformer_forward_store import at, binding, calendar, payload, api


class Stock:
    def stock_exists(self, stock): return True
    def health(self): return True


class Evaluations:
    def list(self, *args): return []
    def get(self, *args): return None


def client(root):
    from api.forecast import create_app
    return TestClient(create_app(Stock(), Evaluations(), root))


def make_store(tmp_path, name='forward_signals_test'):
    return api().ForwardSignalStore(tmp_path / name, binding())


def publish_date(store, signal_date, clock=None, p=None):
    p = p or payload()
    p['signal_date'] = signal_date
    for model in p['models']:
        for row in model['scores']:
            row['date'] = signal_date
    with store.lock(signal_date):
        attempt = store.new_attempt(signal_date)
        return store.publish(signal_date, attempt, p, calendar=calendar(),
                             frozen_at=at(binding()['frozen_at'][:-6]),
                             clock=clock or (lambda: at(signal_date + 'T17:00:00')))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tamper(path):
    body = json.loads(Path(path).read_text(encoding='utf-8'))
    body['tampered'] = True
    Path(path).write_text(json.dumps(body), encoding='utf-8')


def test_latest_packet_schema_consensus_and_integrity(tmp_path):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    folder = store.root / 'signals' / '2026-10-08'
    with client(tmp_path) as http:
        response = http.get('/api/analysis/transformer/signals')
        assert response.status_code == 200
        body = response.json()
    assert set(body) == {
        'signal_date', 'status', 'prospective_eligible', 'published_at', 'deadline',
        'entry_date', 'exit_date', 'frozen_at', 'cutoff', 'store', 'available_dates',
        'primary_policy', 'universe', 'models', 'consensus', 'integrity'}
    assert body['signal_date'] == '2026-10-08'
    assert body['status'] == 'PROSPECTIVE_CANDIDATE' and body['prospective_eligible'] is True
    assert body['published_at'] == '2026-10-08T17:00:00+08:00'
    assert body['deadline'] == '2026-10-09T09:00:00+08:00'
    assert (body['entry_date'], body['exit_date']) == ('2026-10-09', '2026-10-15')
    assert body['frozen_at'] == '2026-10-05T15:54:01+08:00'
    assert body['cutoff'] == '2026-09-30'
    assert body['store'] == 'forward_signals_test'
    assert body['available_dates'] == ['2026-10-08']
    assert body['primary_policy'] == 'raw'
    assert body['universe'] == binding()['codes']
    codes = binding()['codes']
    top5 = [codes[19 - i] for i in range(5)]
    assert len(body['models']) == 6
    ordered = sorted(binding()['models'], key=lambda m: (m['seed'], m['mode']))
    for summary, bound in zip(body['models'], ordered):
        assert set(summary) == {'seed', 'mode', 'date', 'model_sha256', 'top5'}
        assert (summary['seed'], summary['mode']) == (bound['seed'], bound['mode'])
        assert summary['date'] == '2026-10-08'
        assert summary['model_sha256'] == bound['model_sha256']
        assert summary['top5'] == {'raw': top5, 'nonnegative_variance': top5}
    assert len(body['consensus']) == 20
    for index, row in enumerate(body['consensus']):
        assert set(row) == {'ts_code', 'mean_raw', 'mean_nonnegative_variance',
                            'rank_raw', 'rank_nonnegative_variance', 'scores'}
        assert row['ts_code'] == codes[index]
        assert row['mean_raw'] == float(index) and row['mean_nonnegative_variance'] == float(index)
        assert row['rank_raw'] == 20 - index and row['rank_nonnegative_variance'] == 20 - index
        assert len(row['scores']) == 6
        for score, bound in zip(row['scores'], ordered):
            assert set(score) == {'seed', 'mode', 'raw', 'nonnegative_variance'}
            assert (score['seed'], score['mode']) == (bound['seed'], bound['mode'])
            assert score['raw'] == float(index) and score['nonnegative_variance'] == float(index)
    for policy in ('rank_raw', 'rank_nonnegative_variance'):
        assert sorted(row[policy] for row in body['consensus']) == list(range(1, 21))
    expected_binding_sha = hashlib.sha256(
        json.dumps(binding(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    assert body['integrity'] == dict(
        verified=True,
        marker_sha256=sha(folder / 'marker.json'),
        receipt_sha256=sha(folder / 'receipt.json'),
        binding_sha256=expected_binding_sha,
        payload_sha256=sha(folder / 'payload.json'))


def test_explicit_date_latest_selection_and_unknown_date_404(tmp_path):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    publish_date(store, '2026-10-09')
    with client(tmp_path) as http:
        latest = http.get('/api/analysis/transformer/signals')
        assert latest.status_code == 200
        assert latest.json()['signal_date'] == '2026-10-09'
        assert latest.json()['available_dates'] == ['2026-10-08', '2026-10-09']
        older = http.get('/api/analysis/transformer/signals?date=2026-10-08')
        assert older.status_code == 200 and older.json()['signal_date'] == '2026-10-08'
        assert older.json()['available_dates'] == ['2026-10-08', '2026-10-09']
        missing = http.get('/api/analysis/transformer/signals?date=2026-10-12')
        assert missing.status_code == 404
        assert missing.json() == {'detail': 'Resource not found'}


def test_late_diagnostic_is_presented_honestly(tmp_path):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08', clock=lambda: at('2026-10-09T12:22:20'))
    with client(tmp_path) as http:
        body = http.get('/api/analysis/transformer/signals').json()
    assert body['status'] == 'LATE_DIAGNOSTIC'
    assert body['prospective_eligible'] is False
    assert body['deadline'] == '2026-10-09T09:00:00+08:00'
    assert body['published_at'] == '2026-10-09T12:22:20+08:00'


@pytest.mark.parametrize('query', [
    '?date=bad', '?date=', '?date=2026-13-45', '?date=2026-1-08',
    '?date=../binding.json', '?date=2026-10-08&date=2026-10-08',
    '?ts_code=000001.SZ', '?train=true', '?limit=5', '?date=2026-10-08&extra=1',
])
def test_strict_requests_are_400(tmp_path, query):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    with client(tmp_path) as http:
        response = http.get('/api/analysis/transformer/signals' + query)
        assert response.status_code == 400
        assert response.json() == {'detail': 'Invalid request'}


@pytest.mark.parametrize('file', ['payload.json', 'marker.json', 'receipt.json', 'seal.json'])
def test_tampered_packet_is_sanitized_503_without_leaks(tmp_path, file):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    tamper(store.root / 'signals' / '2026-10-08' / file)
    with client(tmp_path) as http:
        response = http.get('/api/analysis/transformer/signals')
        assert response.status_code == 503
        assert response.json() == {'detail': 'Transformer signal dependency unavailable'}
        assert str(tmp_path) not in response.text
        assert 'tampered' not in response.text


@pytest.mark.parametrize('file', ['seal.json', 'receipt.json', 'payload.json'])
def test_incomplete_packet_is_not_discovered(tmp_path, file):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    (store.root / 'signals' / '2026-10-08' / file).unlink()
    with client(tmp_path) as http:
        response = http.get('/api/analysis/transformer/signals')
        assert response.status_code == 404
        assert response.json() == {'detail': 'Resource not found'}


def test_discovery_spans_multiple_store_roots(tmp_path):
    first = make_store(tmp_path, 'forward_signals_20261008')
    publish_date(first, '2026-10-08')
    second = make_store(tmp_path, 'forward_signals_20261009')
    publish_date(second, '2026-10-09')
    with client(tmp_path) as http:
        latest = http.get('/api/analysis/transformer/signals').json()
        assert latest['signal_date'] == '2026-10-09'
        assert latest['store'] == 'forward_signals_20261009'
        assert latest['available_dates'] == ['2026-10-08', '2026-10-09']
        older = http.get('/api/analysis/transformer/signals?date=2026-10-08').json()
        assert older['store'] == 'forward_signals_20261008'


def test_same_date_in_two_roots_fails_closed(tmp_path):
    for name in ('forward_signals_a', 'forward_signals_b'):
        publish_date(make_store(tmp_path, name), '2026-10-08')
    with client(tmp_path) as http:
        for query in ('', '?date=2026-10-08'):
            response = http.get('/api/analysis/transformer/signals' + query)
            assert response.status_code == 503
            assert response.json() == {'detail': 'Transformer signal dependency unavailable'}


def test_missing_or_empty_root_is_404(tmp_path):
    publish_date(make_store(tmp_path / 'nested'), '2026-10-08')
    (tmp_path / 'forward_signals_empty').mkdir()
    (tmp_path / 'forward_signals_no_packet' / 'signals' / '2026-10-08').mkdir(parents=True)
    (tmp_path / 'bare').mkdir()
    for root in (tmp_path / 'does_not_exist', tmp_path / 'bare', tmp_path,
                 tmp_path / 'nested' / 'forward_signals_test'):
        with client(root) as http:
            response = http.get('/api/analysis/transformer/signals')
            assert response.status_code == 404, root
            assert response.json() == {'detail': 'Resource not found'}
    # The nested store root still serves through its own signals root.
    with client(tmp_path / 'nested') as http:
        assert http.get('/api/analysis/transformer/signals').status_code == 200


def test_serving_never_mutates_the_sealed_store(tmp_path):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    with client(tmp_path) as http:
        first = http.get('/api/analysis/transformer/signals')
        second = http.get('/api/analysis/transformer/signals?date=2026-10-08')
        assert first.status_code == second.status_code == 200
        assert first.json() == second.json()
    after = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert before == after


def test_request_path_never_imports_torch(tmp_path):
    code = '''
import os, sys, tempfile
os.environ.setdefault('DB_PASSWORD', 'pytest-only-placeholder')
from pathlib import Path
from fastapi.testclient import TestClient
from tests.test_transformer_forward_store import api, binding
from tests.test_transformer_signals_api import Stock, Evaluations, publish_date, make_store
root = Path(tempfile.mkdtemp())
publish_date(make_store(root), '2026-10-08')
from api.forecast import create_app
with TestClient(create_app(Stock(), Evaluations(), root)) as http:
    response = http.get('/api/analysis/transformer/signals')
    assert response.status_code == 200, response.text
    assert response.json()['signal_date'] == '2026-10-08'
assert 'torch' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True,
                            timeout=300, cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr


def test_cache_revalidates_when_packet_mtime_changes(tmp_path):
    store = make_store(tmp_path)
    publish_date(store, '2026-10-08')
    with client(tmp_path) as http:
        assert http.get('/api/analysis/transformer/signals').status_code == 200
        tamper(store.root / 'signals' / '2026-10-08' / 'payload.json')
        response = http.get('/api/analysis/transformer/signals')
        assert response.status_code == 503
        assert response.json() == {'detail': 'Transformer signal dependency unavailable'}
