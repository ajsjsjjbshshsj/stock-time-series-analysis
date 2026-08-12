import sys
from pathlib import Path


def test_collector_can_import_stock_analysis_database_package():
    import app.main  # noqa: F401

    stock_analysis_root = Path(__file__).resolve().parents[2] / "stock-analysis-app"

    assert str(stock_analysis_root) in sys.path

    from database.db_connector import DatabaseConnector

    assert DatabaseConnector is not None
