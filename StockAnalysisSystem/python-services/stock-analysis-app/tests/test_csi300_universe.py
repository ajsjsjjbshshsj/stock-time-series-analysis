import importlib
import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

NOW = datetime(2026, 10, 9, 15, tzinfo=timezone(timedelta(hours=8)))


def rows(n=300):
    return [dict(index_code='000300.SH', con_code=f'{i+1:06d}.SZ', trade_date='20260930', weight=.25)
            for i in range(n)]


def source(tmp_path, n=300):
    root = tmp_path/'source'
    root.mkdir()
    binding = dict(schema_version=1, source='tushare.index_weight', index_code='000300.SH', requested_as_of='2026-10-09')
    pages = []
    for month, start, end, data in [('202610', '20261001', '20261009', []), ('202609', '20260901', '20260930', rows(n))]:
        params = dict(index_code='000300.SH', start_date=start, end_date=end)
        payload = dict(params=params, acquired_at=NOW.isoformat(), rows=data)
        path = root/f'request_{month}.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        (root/(path.name+'.sha.json')).write_text(json.dumps(dict(sha256=sha)))
        pages.append(dict(path=path.name, params=params, sha256=sha, row_count=len(data)))
    manifest = dict(binding, pages=pages, acquired_at=NOW.isoformat(), provider_snapshot_date='2026-09-30', rows=rows(n))
    (root/'binding.json').write_text(json.dumps(binding))
    (root/'acquisition_manifest.json').write_text(json.dumps(manifest))
    sha = hashlib.sha256((root/'acquisition_manifest.json').read_bytes()).hexdigest()
    (root/'complete.json').write_text(json.dumps(dict(schema_version=1, manifest_sha256=sha)))
    return root, manifest


def module():
    try: return importlib.import_module('analysis.csi300_universe')
    except ModuleNotFoundError: pytest.fail('immutable CSI300 universe contract missing')


def test_mapping_is_stable_and_same_count_different_members_are_different(tmp_path):
    root, manifest = source(tmp_path)
    first = module().build_universe(manifest, rows(), NOW)
    reverse = module().build_universe(manifest, list(reversed(rows())), NOW)
    assert first['stockid2idx'] == reverse['stockid2idx']
    assert first['codes_sha256'] == reverse['codes_sha256']
    assert first['stockid2idx']['000001.SZ'] == 0
    assert first['stockid2idx']['000300.SZ'] == 299
    different = rows()
    different[-1]['con_code'] = '600001.SH'
    other = module().build_universe(dict(manifest, rows=different), different, NOW)
    assert other['pool_version'] != first['pool_version']
    assert first['provider_snapshot_date'] == '2026-09-30'
    assert first['requested_as_of'] == '2026-10-09'


@pytest.mark.parametrize('kind', ['20', '299', '301', 'duplicate', 'BJ', 'wrong_index', 'future'])
def test_bad_identity_cannot_be_frozen(tmp_path, kind):
    _, manifest = source(tmp_path)
    data = rows(int(kind) if kind.isdigit() else 300)
    if kind == 'duplicate': data[-1]['con_code'] = data[0]['con_code']
    if kind == 'BJ': data[-1]['con_code'] = '430001.BJ'
    if kind == 'wrong_index': data[-1]['index_code'] = '000905.SH'
    if kind == 'future': data[-1]['trade_date'] = '20261020'
    with pytest.raises(ValueError): module().build_universe(manifest, data, NOW)


def publish(tmp_path):
    source_root, manifest = source(tmp_path)
    verified_source = module().read_acquisition(source_root)
    version = module().build_universe(verified_source, rows(), NOW)['pool_version']
    output = tmp_path/'csi300_fixed'/version
    result = module().publish_universe(source_root, output, NOW)
    return output, result


def test_sealed_publication_and_verification_are_read_only(tmp_path):
    output, manifest = publish(tmp_path)
    assert len(manifest['codes']) == 300
    before = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    assert module().verify_universe(output) == manifest
    assert before == {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    assert manifest['survivorship_bias'] is True


@pytest.mark.parametrize('kind', ['missing_seal', 'mapping', 'weight', 'time', 'partial'])
def test_partial_or_changed_universe_rejected(tmp_path, kind):
    output, manifest = publish(tmp_path)
    if kind == 'missing_seal': (output/'seal.json').unlink()
    if kind in ('mapping', 'time'):
        field = 'stockid2idx' if kind == 'mapping' else 'frozen_at'
        manifest[field] = {} if kind == 'mapping' else '2020-01-01T00:00:00+08:00'
        (output/'manifest.json').write_text(json.dumps(manifest))
    if kind == 'weight':
        path = output/'source/request_202609.json'
        path.write_bytes(path.read_bytes()+b' ')
    if kind == 'partial': (output/'complete.json').unlink()
    with pytest.raises(ValueError): module().verify_universe(output)


def test_repeat_publication_does_not_overwrite(tmp_path):
    output, _ = publish(tmp_path)
    before = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): module().publish_universe(tmp_path/'source', output, NOW)
    assert before == {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
