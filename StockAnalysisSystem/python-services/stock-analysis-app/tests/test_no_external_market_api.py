import ast
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def scan_python_imports(root: Path, forbidden: set[str]) -> list[str]:
    violations = []
    for path in root.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {item.name.split('.')[0] for item in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split('.')[0]}
            else:
                continue
            if names & forbidden:
                violations.append(str(path.relative_to(root)))
    return sorted(set(violations))


def test_analysis_app_has_no_tushare_or_akshare_imports():
    assert scan_python_imports(PROJECT_ROOT, {'tushare', 'akshare'}) == []


def test_stock_filter_reads_stored_constituent_snapshots():
    from data_processor.stock_filter import resolve_stock_codes

    repository = MagicMock()
    repository.get_industry_constituents.return_value = pd.DataFrame({
        'ts_code': ['600000.SH', '000001.SZ']
    })
    repository.get_index_constituents.return_value = pd.DataFrame({
        'ts_code': ['000001.SZ', '600519.SH']
    })

    result = resolve_stock_codes(
        sectors=['银行'],
        index_codes=['000300.SH'],
        use_tushare=True,
        market_repository=repository,
    )

    assert result == ['000001.SZ', '600000.SH', '600519.SH']
    repository.get_industry_constituents.assert_called_once_with('银行')
    repository.get_index_constituents.assert_called_once_with('000300.SH')
