"""Exercise real file/scaler/metadata audits; replace only costly training/providers."""
import copy
import importlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pytest
import torch

from analysis.transformer_config import TRANSFORMER_CONFIG
from analysis.transformer_experiment import sha256, write_json
from analysis.transformer_features import (feature_columns, normalize_panel, prepare_training_data,
                                          save_model_preprocessing)
from analysis.transformer_model import MultiHeadStockTransformer
from data_processor.adjusted_market_panel import build_model_panel


def api():
    return importlib.import_module('scripts.run_transformer_forward_freeze')


def data():
    dates = pd.to_datetime(['2023-10-09', '2026-06-01', '2026-07-01', '2026-07-20',
                            '2026-07-22', '2026-07-23', '2026-07-27', '2026-07-29',
                            '2026-07-30', '2026-08-03', '2026-09-24', '2026-09-25',
                            '2026-09-28', '2026-09-29', '2026-09-30'])
    raw = pd.DataFrame([dict(ts_code=f'{i:06}.SZ', trade_date=d, open=10.+j,
                             high=30., low=9., close=11.+j, vol=100., amount=100.)
                         for i in range(20) for j, d in enumerate(dates)])
    factors = raw[['ts_code', 'trade_date']].assign(adj_factor=1.)
    panels, configs = {}, {}
    for mode in ('adjusted', 'unadjusted_control'):
        panels[mode], contract = build_model_panel(raw, factors, mode=mode, factor_snapshot_sha256='a'*64)
        saved = dict(TRANSFORMER_CONFIG, seed=42, batch_size=8, num_epochs=30, use_probe_selection=False,
                     early_stopping_patience=5, early_stopping_min_delta=1e-6,
                     score_adjustment_policy='nonnegative_variance', market_preprocessing=contract)
        for seed in (42, 123, 2026):
            configs[(seed, mode)] = dict(saved, seed=seed)
    return panels, configs, dict(source='fixture', cutoff='2026-09-30', codes=sorted(raw.ts_code.unique()))


def fake_feature_cache(panel, *, config, **kwargs):
    folder = Path(config['output_dir'])
    folder.mkdir(parents=True)
    columns = feature_columns('158+39')
    normalized = normalize_panel(panel)
    raw = pd.DataFrame(np.tile(np.arange(len(normalized), dtype=float)[:, None], (1, len(columns))), columns=columns)
    for name in columns:
        if name in normalized:
            raw[name] = normalized[name].fillna(0.)
    raw['日期'], raw['股票代码'] = normalized['日期'], normalized['股票代码']
    mapping = {c: i for i, c in enumerate(sorted(panel.ts_code.unique()))}
    raw['instrument'] = raw['股票代码'].map(mapping)
    grouped = normalized.groupby('股票代码')
    raw['label'] = (grouped['开盘'].shift(-5)-grouped['开盘'].shift(-1))/(grouped['开盘'].shift(-1)+1e-12)
    raw['label_target_date'] = grouped['日期'].shift(-5)
    raw['is_val'] = raw['日期'] >= pd.Timestamp('2026-07-30')
    path = folder/'features_158+39.parquet'
    raw.to_parquet(path, index=False)
    normalized.to_parquet(folder/'raw_panel.parquet', index=False)
    write_json(folder/'features_158+39_stockid2idx.json', mapping)
    write_json(folder/'features_158+39_meta.json', dict(cache_version=2, feature_scale='raw', feature_num='158+39',
        market_preprocessing=config['market_preprocessing'], feature_cols=columns, val_start_date='2026-07-30', num_stocks=20))
    return str(path), columns, '2026-07-30'


def fake_training(*, feature_path, config):
    from analysis.transformer_features import load_feature_cache
    raw, columns, mapping, boundary = load_feature_cache(feature_path, config)
    _, _, _, scaler = prepare_training_data(raw, columns, boundary)
    folder = Path(config['output_dir'])
    write_json(folder/'config.json', config)
    torch.manual_seed(config['seed']+(10000 if config['market_preprocessing']['mode'] == 'adjusted' else 0))
    torch.save(MultiHeadStockTransformer(len(columns), config, 20).state_dict(), folder/'best_model.pth')
    history = [dict(eval_final_score=.1, final_score=.1, train_loss=1., eval_loss=1.)]
    write_json(folder/'training_history.json', history)
    scaler_path = save_model_preprocessing(folder/'best_model.pth', scaler, columns, columns, mapping, config,
        raw['日期'].min(), raw.groupby('股票代码')['日期'].min().to_dict())
    return dict(model_path=str(folder/'best_model.pth'), scaler_path=scaler_path, feature_names=columns,
                stockid2idx=mapping, config=config, best_score=.1, best_epoch=1, epochs_completed=1,
                stopped_early=False, history=history)


def fake_scores(panel, record, date):
    return [dict(date=date, ts_code=c, raw=float(i), nonnegative_variance=float(i),
                 model_sha256=sha256(record['model_path']))
            for i, c in enumerate(sorted(panel.ts_code.unique()))]


@pytest.fixture
def environment(tmp_path, monkeypatch):
    module = api()
    panels, configs, binding = data()
    app = tmp_path/'app'
    (app/'models/transformer').mkdir(parents=True)
    source = app/'models/transformer/multiseed_source'
    source.mkdir()
    write_json(source/'old.json', {'keep': True})
    output = app/'models/transformer/forward_freeze_test'
    monkeypatch.setattr(module, 'APP', app)
    monkeypatch.setattr(module, 'source_inputs', lambda p: (panels, configs, copy.deepcopy(binding)))
    monkeypatch.setattr(module, 'compute_and_save_features', fake_feature_cache)
    monkeypatch.setattr(module, 'run_transformer_training', fake_training)
    monkeypatch.setattr(module, 'latest_scores', fake_scores)
    monkeypatch.setattr(torch.cuda, 'is_available', lambda: True)
    monkeypatch.setattr(torch.cuda, 'get_device_name', lambda i: 'test CUDA')
    monkeypatch.setattr(torch.cuda, 'synchronize', lambda: None)
    return module, source, output, panels, configs


def test_six_new_models_freeze_then_resume_without_training_or_writing(environment, monkeypatch):
    module, source, output, _, _ = environment
    module.run(source, output)
    manifest = module.read_json(output/'freeze_manifest.json')
    assert manifest['status'] == 'FROZEN'
    assert len(manifest['models']) == 6
    assert manifest['rules']['requires_pre_entry_signal'] is True
    assert manifest['frozen_at'].endswith('+08:00')
    assert len({r['artifact_hashes']['model/best_model.pth'] for r in manifest['models']}) == 6
    assert module.verify(source, output)['old_files_checked'] == 1
    before = {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}
    monkeypatch.setattr(module, 'run_transformer_training', lambda **kw: pytest.fail('resume trained'))
    monkeypatch.setattr(module, 'compute_and_save_features', lambda *a, **kw: pytest.fail('resume fitted'))
    module.run(source, output)
    assert before == {str(p): p.read_bytes() for p in output.rglob('*') if p.is_file()}


@pytest.fixture
def completed(environment):
    environment[0].run(environment[1], environment[2])
    return environment


@pytest.mark.parametrize('bad', ['model', 'scaler', 'config', 'features', 'raw_panel', 'history', 'scores', 'rules', 'missing_model', 'old', 'binding'])
def test_verify_rejects_changed_artifacts_or_contract(completed, bad):
    module, source, output, _, _ = completed
    folder = output/'seed_42/adjusted/model'
    files = dict(model='best_model.pth', scaler='best_model_scaler.pkl', config='config.json',
                 features='features_158+39.parquet', raw_panel='raw_panel.parquet', history='training_history.json')
    if bad in files:
        path = folder/files[bad]
        path.write_bytes(path.read_bytes()+b'changed')
    elif bad == 'scores':
        path = output/'seed_42/adjusted/scores.json'
        path.write_bytes(path.read_bytes()+b' ')
    elif bad == 'old':
        write_json(source/'old.json', {'keep': False})
    else:
        path = output/'freeze_manifest.json'
        manifest = module.read_json(path)
        if bad == 'rules':
            manifest['rules']['top_k'] = 1
        elif bad == 'missing_model':
            manifest['models'].pop()
        else:
            manifest['binding']['cutoff'] = '2026-10-01'
        write_json(path, manifest)
    with pytest.raises(ValueError):
        module.verify(source, output)


def test_partial_failed_fold_is_preserved_and_not_frozen(environment, monkeypatch):
    module, source, output, _, _ = environment
    def failed(**kwargs):
        raise RuntimeError('training interrupted')
    monkeypatch.setattr(module, 'run_transformer_training', failed)
    with pytest.raises(RuntimeError):
        module.run(source, output)
    assert not (output/'freeze_manifest.json').exists()
    cache = output/'seed_42/adjusted/model/features_158+39.parquet'
    before = cache.read_bytes()
    with pytest.raises(ValueError, match='[Pp]artial'):
        module.run(source, output)
    assert cache.read_bytes() == before


@pytest.mark.parametrize('bad', ['config', 'scaler', 'scale', 'boundaries', 'history', 'feature_order'])
def test_record_audit_checks_actual_semantics_not_only_claimed_hashes(completed, bad):
    module, _, output, panels, configs = completed
    folder = output/'seed_42/adjusted'
    record = module.read_json(folder/'result.json')
    config = module.frozen_config(configs[(42, 'adjusted')], folder, 42, configs[(42, 'adjusted')]['market_preprocessing'])
    if bad == 'config':
        write_json(folder/'model/config.json', dict(config, batch_size=2))
    elif bad in ('scaler', 'scale'):
        scaler = joblib.load(folder/'model/best_model_scaler.pkl')
        if bad == 'scale':
            scaler.scale_[0] *= 2.
        else:
            scaler.mean_[0] += 1.
        joblib.dump(scaler, folder/'model/best_model_scaler.pkl')
        meta = module.read_json(folder/'model/best_model_preprocessing.json')
        meta['scaler_sha256'] = sha256(folder/'model/best_model_scaler.pkl')
        write_json(folder/'model/best_model_preprocessing.json', meta)
    elif bad == 'boundaries':
        record['boundaries']['train_target_end'] = '2026-07-30'
    elif bad == 'history':
        record['best_epoch'] = 2
    else:
        record['feature_names'].reverse()
    record['artifact_hashes'] = module.artifact_hashes(folder)
    with pytest.raises(ValueError):
        module.validate_record(record, panels['adjusted'], config, folder)


def test_paths_and_source_identity_changes_fail_closed(environment, monkeypatch):
    module, source, output, panels, configs = environment
    with pytest.raises(ValueError):
        module.validate_paths(source, source/'forward_freeze_nested')
    module.run(source, output)
    monkeypatch.setattr(module, 'source_inputs', lambda p: (panels, configs, {'different': True}))
    with pytest.raises(ValueError):
        module.run(source, output)


def test_source_loader_rejects_actual_saved_config_drift(tmp_path, monkeypatch):
    module = api()
    panels, configs, binding = data()
    records = {}
    for (seed, mode), config in configs.items():
        folder = tmp_path/f'{seed}_{mode}'
        folder.mkdir()
        write_json(folder/'config.json', config)
        records[(seed, mode, '2026-08')] = dict(config=config, model_path=str(folder/'best_model.pth'))
    path = tmp_path/'42_adjusted/config.json'
    write_json(path, dict(configs[(42, 'adjusted')], batch_size=2))
    monkeypatch.setattr(module, 'load_experiment', lambda *a: (panels['adjusted'], panels, records, {}, binding))
    with pytest.raises(ValueError, match='config'):
        module.source_inputs(tmp_path)
