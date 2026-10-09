"""Latest diagnostic is historical, label-free, and uses only past sessions."""
import importlib

import numpy as np
import pandas as pd
import pytest
import torch
from sklearn.preprocessing import StandardScaler


def api():
    return importlib.import_module('analysis.transformer_forward_diagnostic')


def scores():
    return [dict(date='2026-09-30', ts_code=f'{i:06}.SZ', raw=float(i),
                 nonnegative_variance=0., model_sha256='abc') for i in range(20)]


def test_diagnostic_keeps_all_stocks_and_ties_break_by_code():
    rows = scores()
    result = api().validate_diagnostic(rows, [r['ts_code'] for r in rows], '2026-09-30', 'abc')
    assert result['kind'] == 'historical_diagnostic'
    assert result['top5']['raw'] == ['000019.SZ', '000018.SZ', '000017.SZ', '000016.SZ', '000015.SZ']
    assert result['top5']['nonnegative_variance'] == ['000000.SZ', '000001.SZ', '000002.SZ', '000003.SZ', '000004.SZ']


@pytest.mark.parametrize('bad', ['missing', 'duplicate', 'unknown', 'date', 'hash', 'nan', 'inf', 'bool', 'order'])
def test_malformed_scores_fail_closed(bad):
    rows = scores()
    codes = [r['ts_code'] for r in rows]
    if bad == 'missing':
        rows.pop()
    elif bad == 'duplicate':
        rows[-1] = rows[0].copy()
    elif bad == 'unknown':
        rows[-1]['ts_code'] = 'X'
    elif bad == 'date':
        rows[0]['date'] = '2026-09-29'
    elif bad == 'hash':
        rows[0]['model_sha256'] = 'wrong'
    elif bad == 'order':
        rows.reverse()
    else:
        rows[0]['raw'] = dict(nan=np.nan, inf=np.inf, bool=True)[bad]
    with pytest.raises(ValueError):
        api().validate_diagnostic(rows, codes, '2026-09-30', 'abc')


def test_real_small_model_uses_truncated_history_once_without_labels(tmp_path, monkeypatch):
    module = api()
    from analysis.transformer_model import MultiHeadStockTransformer
    config = dict(sequence_length=2, d_model=8, nhead=2, num_layers=1,
                  dim_feedforward=16, dropout=0., use_multi_head=True)
    mapping = {f'{i:06}.SZ': i for i in range(20)}
    dates = pd.to_datetime(['2026-09-28', '2026-09-29', '2026-09-30', '2026-10-01'])
    panel = pd.DataFrame([dict(ts_code=c, trade_date=d) for c in mapping for d in dates])
    scaler = StandardScaler().fit(pd.DataFrame({'x': [0., 2.]}))
    metadata = dict(feature_history_start='2026-09-28', stock_history_starts={c: '2026-09-28' for c in mapping},
                    stockid2idx=mapping, selected_features=['x'])
    model = MultiHeadStockTransformer(1, config, 20)
    path = tmp_path/'best_model.pth'
    torch.save(model.state_dict(), path)
    calls = []
    def build(observed, cfg, ids, **kwargs):
        assert observed.trade_date.max() == pd.Timestamp('2026-09-30')
        assert kwargs['include_labels'] is False
        calls.append('build')
        frame = pd.DataFrame([{'日期': d, '股票代码': c, 'instrument': i, 'x': float(j), 'label': np.nan}
                              for c, i in ids.items() for j, d in enumerate(dates[:3])])
        return frame, ['x'], ids, 'unused'
    monkeypatch.setattr(module, 'build_feature_panel', build)
    monkeypatch.setattr(module, 'load_model_preprocessing', lambda *a: (scaler, metadata))
    original = scaler.transform
    def transform(frame):
        calls.append('transform')
        return original(frame)
    monkeypatch.setattr(scaler, 'transform', transform)
    monkeypatch.setattr(scaler, 'fit', lambda *a: pytest.fail('must not fit during inference'))
    record = dict(config=config, model_path=str(path), train_end='2026-09-30')
    rows = module.latest_scores(panel, record, '2026-09-30')
    assert calls == ['build', 'transform']
    assert len(rows) == 20 and all(r['date'] == '2026-09-30' for r in rows)
    assert all(np.isfinite(r['raw']) for r in rows)
    assert sorted(tmp_path.iterdir()) == [path]
