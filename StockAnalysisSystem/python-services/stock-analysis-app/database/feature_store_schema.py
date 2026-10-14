"""Explicit feature-table DDL renderer. Importing this module creates nothing."""
from data_processor.feature_store_contract import FEATURE_COLUMNS, validate_table_name


def table_ddl(table):
    table = validate_table_name(table)
    fields = ',\n  '.join('`' + name + '` DOUBLE NULL' for name in FEATURE_COLUMNS)
    return (
        f'CREATE TABLE `{table}` (\n'
        '  build_id CHAR(32) NOT NULL,\n'
        '  ts_code VARCHAR(10) NOT NULL,\n'
        '  trade_date DATE NOT NULL,\n'
        f'  {fields},\n'
        '  PRIMARY KEY (ts_code, trade_date),\n'
        '  KEY idx_feature_date (trade_date)\n'
        ') ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin'
    )
