"""Pure, explicitly versioned price adapter; raw quantity never gets adjusted."""
import hashlib
import json

import numpy as np
import pandas as pd

LEGACY_CONTRACT = {'mode': 'legacy_unadjusted'}


def contract_text(contract):
    return json.dumps(contract, sort_keys=True, separators=(',', ':'), allow_nan=False)


def validate_contract(contract):
    """Validate supported explicit modes before trusting a row marker or sidecar."""
    if contract == LEGACY_CONTRACT:
        return contract
    if not isinstance(contract,dict):
        raise ValueError('Invalid market price contract')
    required={'version','mode','bases','factor_snapshot_sha256','volume_unit','amount_unit','vwap_unit','origin'}
    if (set(contract)!=required or contract['version']!=1 or contract['mode'] not in ('adjusted','unadjusted_control')
            or contract['volume_unit']!='lots' or contract['amount_unit']!='thousand_CNY'
            or contract['vwap_unit']!='CNY_per_share' or contract['origin']!='fixed_first_observed_factor'):
        raise ValueError('Unsupported market price contract')
    bases=contract['bases']
    digest=contract['factor_snapshot_sha256']
    try:
        valid=(isinstance(bases,dict) and all(isinstance(code,str) and np.isfinite(value) and value>0 for code,value in bases.items())
            and isinstance(digest,str) and len(digest)==64 and all(char in '0123456789abcdef' for char in digest)
            and (bool(bases) if contract['mode']=='adjusted' else not bases))
    except (TypeError,ValueError):
        valid=False
    if not valid:
        raise ValueError('Invalid market price contract bases/hash')
    return contract


def validate_market_contract(panel, config):
    """Fail closed on explicit price modes; absent metadata means legacy only."""
    contract = validate_contract(config.get('market_preprocessing', LEGACY_CONTRACT))
    if contract == LEGACY_CONTRACT:
        if '_market_contract' in panel or '_model_vwap' in panel:
            raise ValueError('Legacy market price contract forbids adapted columns')
        return contract
    if '_model_vwap' not in panel:
        raise ValueError('Market price contract requires adapted VWAP')
    values=pd.to_numeric(panel['_model_vwap'],errors='coerce')
    if not np.isfinite(values).all() or (values<0).any():
        raise ValueError('Market price contract requires finite VWAP')
    if '_market_contract' in panel:
        markers = panel['_market_contract']
        if markers.isna().any() or set(markers) != {contract_text(contract)}:
            raise ValueError('Market price contract mismatch')
    elif contract != LEGACY_CONTRACT or '_model_vwap' in panel:
        raise ValueError('Market price contract missing')
    return contract


def build_model_panel(raw, factors, *, mode='adjusted', bases=None, factor_snapshot_sha256=None):
    """Use a fixed historical origin, never a future/end-of-test normalization.

    OHLC and derived VWAP share a multiplier. Actual lots and thousand-CNY
    turnover are preserved, not divided by a factor that includes dividends.
    """
    if mode not in ('adjusted', 'unadjusted_control'):
        raise ValueError('Unsupported market price mode')
    panel = raw.copy()
    required = ['ts_code','trade_date','open','high','low','close','vol','amount']
    if panel.empty or not set(required).issubset(panel) or '_market_contract' in panel:
        raise ValueError('Expected original daily market panel')
    panel['trade_date'] = pd.to_datetime(panel.trade_date.astype(str), errors='raise')
    if panel[['ts_code','trade_date']].isna().any().any() or panel.duplicated(['ts_code','trade_date']).any():
        raise ValueError('Duplicate or missing market identities')
    for name in required[2:]:
        panel[name] = pd.to_numeric(panel[name], errors='raise').astype(float)
    if (not np.isfinite(panel[required[2:]].to_numpy()).all()
            or (panel[['open','high','low','close']] <= 0).any().any()
            or (panel[['vol','amount']] < 0).any().any()):
        raise ValueError('Invalid market observations')
    panel = panel.sort_values(['ts_code','trade_date']).reset_index(drop=True)
    fac = factors.copy()
    if fac.empty or not {'ts_code','trade_date','adj_factor'}.issubset(fac):
        raise ValueError('Factor schema unavailable')
    fac['trade_date'] = pd.to_datetime(fac.trade_date.astype(str), errors='raise')
    fac['adj_factor'] = pd.to_numeric(fac.adj_factor, errors='raise').astype(float)
    if (fac[['ts_code','trade_date']].isna().any().any()
            or fac.duplicated(['ts_code','trade_date']).any()
            or not np.isfinite(fac.adj_factor).all() or (fac.adj_factor <= 0).any()):
        raise ValueError('Invalid or duplicate factor observations')
    panel = panel.merge(fac[['ts_code','trade_date','adj_factor']],
                        on=['ts_code','trade_date'], how='left', validate='one_to_one')
    if panel.adj_factor.isna().any():
        raise ValueError('Factor coverage incomplete')
    first = panel.groupby('ts_code').adj_factor.first().to_dict()
    bases = dict(first if bases is None else bases)
    if set(bases) != set(first) or any(not np.isfinite(v) or v <= 0 for v in bases.values()):
        raise ValueError('Invalid frozen factor bases')
    multiplier = panel.adj_factor / panel.ts_code.map(bases) if mode == 'adjusted' else pd.Series(1., index=panel.index)
    if not np.isfinite(multiplier).all():
        raise ValueError('Nonfinite factor multiplier')
    panel['_model_vwap'] = np.divide(panel.amount.to_numpy()*10, panel.vol.to_numpy(),
        out=np.zeros(len(panel)), where=panel.vol.to_numpy()>0) * multiplier
    for name in ('open','high','low','close'):
        panel[name] *= multiplier
    if not np.isfinite(panel[['open','high','low','close','_model_vwap']].to_numpy()).all():
        raise ValueError('Nonfinite adjusted prices')
    factor_hash = factor_snapshot_sha256 or hashlib.sha256(
        fac.sort_values(['ts_code','trade_date']).to_csv(index=False).encode()).hexdigest()
    contract = dict(version=1, mode=mode, bases=bases if mode == 'adjusted' else {},
        factor_snapshot_sha256=factor_hash, volume_unit='lots', amount_unit='thousand_CNY',
        vwap_unit='CNY_per_share', origin='fixed_first_observed_factor')
    validate_contract(contract)
    panel['_market_contract'] = contract_text(contract)
    return panel, contract
