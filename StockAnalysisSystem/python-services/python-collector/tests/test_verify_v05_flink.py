import importlib.util
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_v05_flink.py"
SPEC = importlib.util.spec_from_file_location("verify_v05_flink", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def fixture_rows(count=20, ts_code="V05TEST.SZ"):
    return [
        {
            "eventId": f"V05:{ts_code}:{day}:original",
            "traceId": "v05-unit",
            "tsCode": ts_code,
            "tradeDate": (date(2026, 8, 1) + timedelta(days=day - 1)).isoformat(),
            "open": Decimal(day),
            "high": Decimal(day),
            "low": Decimal(day),
            "close": Decimal(day),
            "preClose": Decimal(day),
            "change": Decimal(0),
            "pctChg": Decimal("1.2345675"),
            "volume": Decimal(day * 10),
            "amount": Decimal(day * 100),
            "source": "V05_VERIFIER",
            "eventTime": "2026-08-01T15:00:00+08:00",
            "ingestTime": "2026-08-01T16:00:00+08:00",
            "schemaVersion": 1,
        }
        for day in range(1, count + 1)
    ]


def result_record(expected):
    return {
        "key": expected["tsCode"],
        "value": {**expected, "calculationTime": "2026-09-30T10:00:00+08:00"},
    }


def test_expected_indicators_use_decimal_half_up_and_full_window():
    rows = fixture_rows(20)
    result = MODULE.expected_indicators(rows)[-1]

    assert result["windowSize"] == 20
    assert result["isWarmup"] is False
    assert result["ma5"] == Decimal("18.000000")
    assert result["ma10"] == Decimal("15.500000")
    assert result["ma20"] == Decimal("10.500000")
    assert result["volMa5"] == Decimal("180.000000")
    assert result["volMa10"] == Decimal("155.000000")
    assert result["volumeRatio"] == Decimal("1.111111")
    assert result["pctChg"] == Decimal("1.234568")


def test_expected_indicators_do_not_expose_partial_averages():
    results = MODULE.expected_indicators(fixture_rows(10))

    assert results[3]["ma5"] is None
    assert results[4]["ma5"] == Decimal("3.000000")
    assert results[8]["ma10"] is None
    assert results[9]["ma10"] == Decimal("5.500000")
    assert results[9]["ma20"] is None
    assert all(item["isWarmup"] for item in results)


def test_same_day_overwrite_replaces_last_point_without_growing_window():
    rows = fixture_rows(20)
    overwrite = {**rows[-1], "eventId": "V05:overwrite", "close": Decimal(40), "volume": Decimal(400)}
    results = MODULE.expected_indicators([*rows, overwrite])

    assert results[-1]["windowSize"] == 20
    assert results[-1]["ma5"] == Decimal("22.000000")
    assert results[-1]["volMa5"] == Decimal("220.000000")
    assert results[-1]["sourceEventId"] == "V05:overwrite"


def test_validate_results_rejects_duplicate_event_id():
    event = result_record(MODULE.expected_indicators(fixture_rows(1))[0])

    with pytest.raises(AssertionError, match="duplicate eventId"):
        MODULE.validate_results([event, event])


def test_validate_results_checks_key_and_every_expected_field():
    expected = MODULE.expected_indicators(fixture_rows(1))[0]
    record = result_record(expected)
    MODULE.validate_results([record], [expected])

    with pytest.raises(AssertionError, match="Kafka key"):
        MODULE.validate_results([{**record, "key": "wrong"}], [expected])
    with pytest.raises(AssertionError, match="close"):
        MODULE.validate_results([{**record, "value": {**record["value"], "close": Decimal(999)}}], [expected])


def test_validate_results_rejects_missing_and_unexpected_source_events():
    expected = MODULE.expected_indicators(fixture_rows(2))

    with pytest.raises(AssertionError, match="count"):
        MODULE.validate_results([result_record(expected[0])], expected)
    with pytest.raises(AssertionError, match="sourceEventId"):
        MODULE.validate_results([result_record(expected[0]), result_record({**expected[1], "sourceEventId": "other"})], expected)


def test_validate_dead_letter_preserves_original_record_and_error_type():
    source = {"key": "V05TEST.SZ", "raw": b'{"eventId":', "topic": "stock.ods.daily.v1", "partition": 2, "offset": 7}
    record = {"key": source["key"], "value": {
        "originalTopic": source["topic"], "originalPartition": 2, "originalOffset": 7,
        "originalKey": source["key"], "originalPayload": source["raw"].decode(),
        "errorType": "JSON_PARSE", "errorMessage": "invalid StockDailyEvent JSON",
        "failedAt": "2026-09-30T10:00:00+08:00", "schemaVersion": 1,
    }}

    MODULE.validate_dead_letter(record, source, "JSON_PARSE")
    with pytest.raises(AssertionError, match="originalPayload"):
        MODULE.validate_dead_letter({**record, "value": {**record["value"], "originalPayload": "changed"}}, source, "JSON_PARSE")


def test_consumer_config_isolation_and_unique_group():
    first = MODULE.consumer_config("localhost:9092")
    second = MODULE.consumer_config("localhost:9092")

    assert first["isolation.level"] == "read_committed"
    assert first["auto.offset.reset"] == "earliest"
    assert first["group.id"].startswith("v05-verifier-")
    assert first["group.id"] != second["group.id"]


def test_build_rows_use_unique_source_ids_and_numeric_decimals():
    rows = MODULE.build_rows("V05TEST.SZ", "test-run", 2)

    assert [row["tradeDate"] for row in rows] == ["2026-08-01", "2026-08-02"]
    assert len({row["eventId"] for row in rows}) == 2
    assert all(row["tsCode"] == "V05TEST.SZ" for row in rows)
    assert rows[1]["volume"] == Decimal(20)


def test_find_running_job_rejects_missing_or_ambiguous_job():
    jobs = [
        {"jid": "old", "name": "stock-daily-indicator-v1", "state": "FAILED"},
        {"jid": "current", "name": "stock-daily-indicator-v1", "state": "RUNNING"},
    ]

    assert MODULE.find_running_job(jobs) == "current"
    with pytest.raises(AssertionError, match="exactly one"):
        MODULE.find_running_job(jobs[:1])
    with pytest.raises(AssertionError, match="exactly one"):
        MODULE.find_running_job([jobs[1], {**jobs[1], "jid": "second"}])


def test_wait_for_job_running_retries_during_taskmanager_restart(monkeypatch):
    attempts = iter([AssertionError("RESTARTING"), OSError("connection reset"), {"state": "RUNNING"}])

    def check(*_args):
        outcome = next(attempts)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(MODULE, "job_state", check)
    monkeypatch.setattr(MODULE.time, "sleep", lambda _seconds: None)

    assert MODULE.wait_for_job_running("http://flink", "job", 5) == {"state": "RUNNING"}


def test_recovery_after_uses_saved_job_id_during_restart(monkeypatch):
    manifest = {"jobId": "saved-job"}
    monkeypatch.setattr(MODULE, "resolve_job_id", lambda *_args: pytest.fail("discovery during restart"))

    assert MODULE.choose_job_id(None, "recovery-after", manifest, "http://flink", 5) == "saved-job"
    assert MODULE.choose_job_id("explicit", "recovery-after", manifest, "http://flink", 5) == "explicit"
