import pandas as pd


def _panel(turnover_rate=1.25):
    return pd.DataFrame({
        'ts_code': ['000001.SZ'],
        'trade_date': pd.to_datetime(['2026-08-14']),
        'close': [10.2],
        'turnover_rate': [turnover_rate],
    })


def test_raw_panel_cache_keeps_turnover_rate(tmp_path):
    from data_processor.panel_builder import save_raw_panel

    path = tmp_path / 'raw_panel.parquet'
    joined_panel = _panel()
    save_raw_panel(joined_panel, path)

    cached = pd.read_parquet(path)
    assert 'turnover_rate' in cached.columns
    assert cached.loc[0, 'turnover_rate'] == joined_panel.loc[0, 'turnover_rate']


def test_cache_fallback_fills_only_missing_database_values(tmp_path):
    from data_processor.panel_builder import restore_turnover_rate_from_cache

    path = tmp_path / 'raw_panel.parquet'
    cached = pd.DataFrame({
        'ts_code': ['000001.SZ', '600000.SH'],
        'trade_date': pd.to_datetime(['2026-08-14', '2026-08-14']),
        'turnover_rate': [9.99, 2.5],
    })
    cached.to_parquet(path, index=False)
    database_panel = pd.DataFrame({
        'ts_code': ['000001.SZ', '600000.SH'],
        'trade_date': pd.to_datetime(['2026-08-14', '2026-08-14']),
        'turnover_rate': [1.25, None],
    })

    result = restore_turnover_rate_from_cache(database_panel, path)

    assert result.loc[0, 'turnover_rate'] == 1.25
    assert result.loc[1, 'turnover_rate'] == 2.5


def test_cache_names_and_streamlit_cache_decorator_remain_unchanged():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    panel_source = (root / 'data_processor' / 'panel_builder.py').read_text(
        encoding='utf-8'
    )
    dashboard_source = (root / 'visualization' / 'dashboard.py').read_text(
        encoding='utf-8'
    )

    assert "'raw_panel.parquet'" in panel_source
    assert "'panel_features.parquet'" in panel_source
    assert '@st.cache_data' in dashboard_source
