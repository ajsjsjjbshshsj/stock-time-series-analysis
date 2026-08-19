from pathlib import Path
import re


MIGRATION = (
    Path(__file__).resolve().parents[3]
    / 'infrastructure'
    / 'mysql'
    / 'migrations'
    / 'V0.4_001_market_reference_tables.sql'
)


def test_market_reference_tables_use_legacy_compatible_collation():
    sql = MIGRATION.read_text(encoding='utf-8')

    assert sql.count(
        'DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci'
    ) == 2
    for table in ('stock_daily_basic', 'stock_constituent'):
        assert re.search(
            rf'ALTER TABLE `{table}`\s+'
            r'CONVERT TO CHARACTER SET utf8mb4 '
            r'COLLATE utf8mb4_general_ci;',
            sql,
        )
