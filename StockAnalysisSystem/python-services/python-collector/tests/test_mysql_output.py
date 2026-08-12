from unittest.mock import MagicMock

from app.outputs.mysql_output import MysqlOutput
from app.outputs.output_result import DailyOutputBatch


def create_batch(record_count: int = 2) -> DailyOutputBatch:
    records = [
        {
            "ts_code": f"00000{index}.SZ",
            "trade_date": "2026-08-07",
            "open": 10.0,
            "high": 10.5,
            "low": 9.8,
            "close": 10.2,
            "pre_close": 10.0,
            "change": 0.2,
            "pct_chg": 2.0,
            "vol": 100000.0,
            "amount": 1020000.0,
        }
        for index in range(1, record_count + 1)
    ]

    return DailyOutputBatch(
        records=records,
        events=[],
    )


def test_mysql_output_saves_all_records():
    repository = MagicMock()
    repository.save_daily_records.return_value = 2

    output = MysqlOutput(repository)
    batch = create_batch(2)

    result = output.deliver(batch)

    repository.save_daily_records.assert_called_once_with(
        batch.records
    )

    assert result.output_type == "MYSQL"
    assert result.expected_count == 2
    assert result.success_count == 2
    assert result.failure_count == 0
    assert result.errors == []
    assert result.success is True


def test_mysql_output_handles_empty_batch():
    repository = MagicMock()
    output = MysqlOutput(repository)

    result = output.deliver(create_batch(0))

    repository.save_daily_records.assert_not_called()

    assert result.expected_count == 0
    assert result.success_count == 0
    assert result.failure_count == 0
    assert result.errors == []
    assert result.success is True


def test_mysql_output_reports_partial_write():
    repository = MagicMock()
    repository.save_daily_records.return_value = 1

    output = MysqlOutput(repository)
    result = output.deliver(create_batch(2))

    assert result.expected_count == 2
    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.success is False


def test_mysql_output_converts_exception_to_failure_result():
    repository = MagicMock()
    repository.save_daily_records.side_effect = RuntimeError(
        "数据库连接失败"
    )

    output = MysqlOutput(repository)
    result = output.deliver(create_batch(2))

    assert result.output_type == "MYSQL"
    assert result.expected_count == 2
    assert result.success_count == 0
    assert result.failure_count == 2
    assert result.errors == ["数据库连接失败"]
    assert result.success is False


def test_mysql_output_rejects_invalid_repository_count():
    repository = MagicMock()
    repository.save_daily_records.return_value = 5

    output = MysqlOutput(repository)
    result = output.deliver(create_batch(2))

    assert result.success_count == 0
    assert result.failure_count == 2
    assert "写入数量不合法" in result.errors[0]