"""Explicit, read-only CSI300 source adapter; no legacy cache or provider calls."""
from pathlib import Path
import pandas as pd
from analysis.csi300_universe import read, digest, iso_date
from analysis.csi300_readiness import normalize, frame_identity, validate_pool


def factor_source(manifest_path, *, data_path_override=None):
    proof = read(manifest_path)
    if proof.get('source') != 'tushare.adj_factor' or proof.get('complete') is not True:
        raise ValueError('Factor source evidence invalid')
    path = Path(data_path_override) if data_path_override is not None else Path(proof['data_path'])
    if not path.is_absolute(): path = Path(manifest_path).parent/path
    if digest(path) != proof['data_sha256']: raise ValueError('Factor source hash mismatch')
    frame = normalize(pd.read_parquet(path))
    start, end = pd.Timestamp(iso_date(proof['start'])), pd.Timestamp(iso_date(proof['end']))
    if (start > end or not {'ts_code','trade_date','adj_factor'}.issubset(frame) or frame.empty
            or sorted(frame.ts_code.unique()) != sorted(proof['codes'])
            or frame.duplicated(['ts_code','trade_date']).any() or frame.trade_date.isna().any()
            or (frame.trade_date < start).any() or (frame.trade_date > end).any()):
        raise ValueError('Factor source coverage invalid')
    return frame, dict(manifest_path=str(Path(manifest_path).resolve()), data_path=str(path.resolve()),
                        manifest_sha256=digest(manifest_path), data_sha256=digest(path))


def read_local_sources(universe, start_date, end_date, *, connector=None, factor_manifest=None):
    codes = validate_pool(universe)
    if iso_date(start_date) > iso_date(end_date): raise ValueError('Source query range invalid')
    factors, proof = None, None
    if factor_manifest is not None: factors, proof = factor_source(factor_manifest)
    try:
        from database import repository
        if connector is None:
            from database.db_connector import DatabaseConnector
            connector = DatabaseConnector()
        with connector as db:
            with db.session_scope() as session:
                basic = normalize(repository.load_stock_basic_df(session), 'list_date')
                daily = normalize(repository.load_daily_panel(session, codes, start_date, end_date))
    except Exception:
        raise ValueError('SOURCE_UNAVAILABLE: read-only market database unavailable') from None
    if not daily.empty and (not set(daily.ts_code).issubset(codes)
            or (daily.trade_date < pd.Timestamp(start_date)).any() or (daily.trade_date > pd.Timestamp(end_date)).any()):
        raise ValueError('Source query identity/range mismatch')
    basic = basic[basic.ts_code.isin(codes)].copy() if 'ts_code' in basic else basic
    if factors is not None:
        factors = factors[factors.ts_code.isin(codes) & factors.trade_date.between(start_date,end_date)].copy()
    identity = dict(source_mode='strict_database', queries=2, codes=codes, pool_version=universe['pool_version'],
         start_date=start_date, end_date=end_date, daily_sha256=frame_identity(daily), basic_sha256=frame_identity(basic),
         factors_sha256=frame_identity(factors), factor_verified=proof is not None, factor_evidence=proof,
         daily_row_count=len(daily), basic_row_count=len(basic))
    return daily, basic, factors, identity
