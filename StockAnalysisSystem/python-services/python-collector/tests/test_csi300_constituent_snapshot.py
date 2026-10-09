import importlib
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import pandas as pd
import pytest

EPOCH = datetime(2026, 10, 9, 15, tzinfo=timezone(timedelta(hours=8))).timestamp()


def rows(day='20260930', n=300):
    return pd.DataFrame([dict(index_code='000300.SH', con_code=f'{i+1:06d}.SZ',
                             trade_date=day, weight=.25) for i in range(n)])


class Client:
    def __init__(self, response=None):
        self.response, self.calls = rows() if response is None else response, []
    def index_weight(self, **kwargs):
        self.calls.append(kwargs)
        return pd.DataFrame(columns=rows().columns) if kwargs['start_date'] == '20261001' else self.response


def module():
    try: return importlib.import_module('app.market_data.csi300_constituent_snapshot')
    except ModuleNotFoundError: pytest.fail('CSI300 raw evidence acquisition is missing')


def collect(client, tmp_path, **kwargs):
    return module().collect_constituent_evidence(client, '2026-10-09', tmp_path/'source',
        request_state=tmp_path/'request_state.json', clock=lambda: EPOCH, sleep=lambda s: None, **kwargs)


def test_request_date_source_date_and_weights_are_distinct(tmp_path):
    client = Client()
    result = collect(client, tmp_path)
    assert result['requested_as_of'] == '2026-10-09'
    assert result['provider_snapshot_date'] == '2026-09-30'
    assert len(result['rows']) == 300
    assert sum(r['weight'] for r in result['rows']) == 75.  # do not normalize source weights
    assert [c['start_date'] for c in client.calls] == ['20261001', '20260901']
    assert client.calls[0]['end_date'] == '20261009'


@pytest.mark.parametrize('kind', ['299', '301', 'duplicate', 'wrong_index', 'future', 'extra_field'])
def test_bad_latest_snapshot_does_not_fall_back_to_older_full_group(tmp_path, kind):
    newer = rows('20260930', int(kind) if kind.isdigit() else 300)
    if kind == 'duplicate': newer.loc[299, 'con_code'] = newer.loc[0, 'con_code']
    if kind == 'wrong_index': newer.loc[0, 'index_code'] = '000905.SH'
    if kind == 'future': newer.loc[0, 'trade_date'] = '20261020'
    if kind == 'extra_field': newer['unknown'] = 'opaque'
    client = Client(pd.concat([rows('20260901'), newer], ignore_index=True))
    with pytest.raises(ValueError): collect(client, tmp_path)
    assert not (tmp_path/'source/complete.json').exists()
    assert len(client.calls) == 2


def test_completed_source_repeat_is_strictly_read_only(tmp_path):
    collect(Client(), tmp_path)
    files = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    class NoCalls:
        def index_weight(self, **kwargs): pytest.fail('completed source must not call provider')
    again = collect(NoCalls(), tmp_path)
    assert len(again['rows']) == 300
    assert files == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_shared_known_quota_blocks_all_new_calls(tmp_path):
    (tmp_path/'request_state.json').write_text(json.dumps(dict(retry_not_before=EPOCH+100, failure='MINUTE_QUOTA')))
    client = Client()
    with pytest.raises(RuntimeError, match='COOLDOWN'): collect(client, tmp_path)
    assert not client.calls


def test_future_requested_date_rejected_before_provider(tmp_path):
    client = Client()
    with pytest.raises(ValueError):
        module().collect_constituent_evidence(client, '2026-10-10', tmp_path/'source',
             request_state=tmp_path/'state.json', clock=lambda: EPOCH, sleep=lambda s: None)
    assert not client.calls


def test_source_hash_tamper_rejected(tmp_path):
    collect(Client(), tmp_path)
    file = tmp_path/'source/request_202609.json'
    file.write_bytes(file.read_bytes()+b' ')
    with pytest.raises(ValueError): collect(Client(), tmp_path)


def test_empty_response_is_bounded_to_three_months(tmp_path):
    client = Client(pd.DataFrame(columns=rows().columns))
    with pytest.raises(ValueError, match='NO_SNAPSHOT'): collect(client, tmp_path)
    assert len(client.calls) == 3
