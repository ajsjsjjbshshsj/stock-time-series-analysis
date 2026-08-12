from datetime import date
from unittest.mock import MagicMock

import pandas as pd

from app.jobs.daily_collection_job import DailyCollectionJob
from app.models.collection_task import TaskStatus
from app.outputs.output_result import OutputResult


def _collector():
    collector = MagicMock()
    collector.source_name = "tushare"
    collector.collect_daily.return_value = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "trade_date": [date(2026, 8, 7)],
        "open": [10.0],
        "high": [10.5],
        "low": [9.8],
        "close": [10.2],
        "pre_close": [10.0],
        "change": [0.2],
        "pct_chg": [2.0],
        "vol": [100000.0],
        "amount": [1020000.0],
    })
    return collector


def _task_repository():
    repository = MagicMock()
    repository.is_task_success.return_value = False
    repository.create_task.return_value = 42
    return repository


def _result(output_type, success_count, failure_count, errors=None):
    return OutputResult(
        output_type=output_type,
        expected_count=success_count + failure_count,
        success_count=success_count,
        failure_count=failure_count,
        errors=errors or [],
    )


def test_daily_job_routes_records_and_events_through_output():
    output = MagicMock()
    output.deliver.return_value = _result("KAFKA", 1, 0)
    stock_repository = MagicMock()
    task_repository = _task_repository()
    job = DailyCollectionJob(
        _collector(),
        stock_repository,
        task_repository,
        output=output,
    )

    result = job.execute("20260807")

    batch = output.deliver.call_args.args[0]
    assert len(batch.records) == 1
    assert len(batch.events) == 1
    assert batch.events[0].trace_id == "daily-tushare-20260807-42"
    assert result["status"] == TaskStatus.SUCCESS.value
    assert result["output"] == "KAFKA"
    stock_repository.save_daily_records.assert_not_called()


def test_daily_job_marks_one_successful_dual_destination_as_partial():
    mysql = _result("MYSQL", 1, 0)
    kafka = _result("KAFKA", 0, 1, ["broker unavailable"])
    output = MagicMock()
    output.deliver.return_value = OutputResult(
        output_type="DUAL",
        expected_count=1,
        success_count=0,
        failure_count=1,
        errors=["KAFKA: broker unavailable"],
        details={"MYSQL": mysql, "KAFKA": kafka},
    )
    task_repository = _task_repository()
    job = DailyCollectionJob(
        _collector(),
        MagicMock(),
        task_repository,
        output=output,
    )

    result = job.execute("20260807")

    assert result["success"] is False
    assert result["status"] == TaskStatus.PARTIAL_SUCCESS.value
    assert result["record_count"] == 1
    assert result["deliveries"]["MYSQL"]["success"] is True
    assert result["deliveries"]["KAFKA"]["success"] is False
    _, status, = task_repository.update_status.call_args.args[:2]
    assert status == TaskStatus.PARTIAL_SUCCESS.value


def test_daily_job_marks_output_failure_as_failed():
    output = MagicMock()
    output.deliver.return_value = _result(
        "KAFKA", 0, 1, ["broker unavailable"]
    )
    task_repository = _task_repository()
    job = DailyCollectionJob(
        _collector(),
        MagicMock(),
        task_repository,
        output=output,
    )

    result = job.execute("20260807")

    assert result["success"] is False
    assert result["status"] == TaskStatus.FAILED.value
    assert result["error"] == "broker unavailable"
