from unittest.mock import MagicMock

from app.outputs.dual_output import DualOutput
from app.outputs.output_result import DailyOutputBatch, OutputResult


def _result(output_type: str, success_count: int, failure_count: int):
    return OutputResult(
        output_type=output_type,
        expected_count=success_count + failure_count,
        success_count=success_count,
        failure_count=failure_count,
        errors=[] if failure_count == 0 else ["delivery failed"],
    )


def test_dual_output_delivers_to_both_destinations():
    mysql = MagicMock()
    kafka = MagicMock()
    mysql.deliver.return_value = _result("MYSQL", 2, 0)
    kafka.deliver.return_value = _result("KAFKA", 2, 0)
    batch = DailyOutputBatch(records=[{}, {}], events=[])

    result = DualOutput(mysql, kafka).deliver(batch)

    mysql.deliver.assert_called_once_with(batch)
    kafka.deliver.assert_called_once_with(batch)
    assert result.success is True
    assert result.success_count == 2
    assert result.details["MYSQL"].success is True
    assert result.details["KAFKA"].success is True


def test_dual_output_reports_partial_delivery():
    mysql = MagicMock()
    kafka = MagicMock()
    mysql.deliver.return_value = _result("MYSQL", 2, 0)
    kafka.deliver.return_value = _result("KAFKA", 0, 2)

    result = DualOutput(mysql, kafka).deliver(
        DailyOutputBatch(records=[{}, {}], events=[])
    )

    assert result.success is False
    assert result.success_count == 0
    assert result.failure_count == 2
    assert result.errors == ["KAFKA: delivery failed"]


def test_dual_output_still_calls_kafka_when_mysql_raises():
    mysql = MagicMock()
    kafka = MagicMock()
    mysql.deliver.side_effect = RuntimeError("mysql unavailable")
    kafka.deliver.return_value = _result("KAFKA", 1, 0)

    result = DualOutput(mysql, kafka).deliver(
        DailyOutputBatch(records=[{}], events=[])
    )

    kafka.deliver.assert_called_once()
    assert result.details["MYSQL"].failure_count == 1
    assert result.details["KAFKA"].success is True
