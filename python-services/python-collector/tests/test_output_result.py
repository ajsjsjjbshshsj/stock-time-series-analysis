from app.outputs.output_result import DailyOutputBatch, OutputResult


def test_output_result_is_successful_without_failures():
    result = OutputResult(
        output_type="MYSQL",
        expected_count=2,
        success_count=2,
        failure_count=0,
        errors=[],
    )

    assert result.success is True


def test_output_result_is_not_successful_with_failures():
    result = OutputResult(
        output_type="MYSQL",
        expected_count=2,
        success_count=1,
        failure_count=1,
        errors=["one record failed"],
    )

    assert result.success is False


def test_daily_output_batch_keeps_records_and_events():
    records = [{"ts_code": "000001.SZ"}]
    events = []

    batch = DailyOutputBatch(records=records, events=events)

    assert batch.records == records
    assert batch.events == events


def test_empty_daily_output_batch_is_allowed():
    batch = DailyOutputBatch(records=[], events=[])

    assert batch.records == []
    assert batch.events == []
