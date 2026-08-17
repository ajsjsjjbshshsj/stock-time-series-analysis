import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify_v03_smoke.py"
SPEC = importlib.util.spec_from_file_location("verify_v03_smoke", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_builds_valid_messages_with_stable_key_and_unique_event_id():
    messages = MODULE.build_messages("valid", 2, "trace-smoke")

    assert [message["key"] for message in messages] == ["000001.SZ", "000001.SZ"]
    assert messages[0]["payload"]["traceId"] == "trace-smoke"
    assert messages[0]["payload"]["eventId"] != messages[1]["payload"]["eventId"]


def test_builds_failure_isolation_scenarios():
    malformed = MODULE.build_messages("malformed", 1, "trace")[0]
    key_mismatch = MODULE.build_messages("key-mismatch", 1, "trace")[0]
    unsupported = MODULE.build_messages("unsupported-schema", 1, "trace")[0]

    assert malformed["raw"] == b'{"eventId":'
    assert malformed["key"] == "000003.SZ"
    assert key_mismatch["key"] != key_mismatch["payload"]["tsCode"]
    assert unsupported["payload"]["schemaVersion"] == 2


def test_calculates_java_monitoring_deltas():
    baseline = {"totalConsumed": 10, "successCount": 8, "failureCount": 2}
    current = {"totalConsumed": 14, "successCount": 11, "failureCount": 3}

    assert MODULE.calculate_deltas(baseline, current) == {
        "totalConsumed": 4,
        "successCount": 3,
        "failureCount": 1,
    }


def test_matches_dead_letter_by_key_and_error_header():
    expected = {
        "key": "000001.SZ",
        "raw": b'{"schemaVersion":2}',
        "errorType": "unsupported_schema",
        "sourceTopic": "stock.ods.daily.v1",
        "sourcePartition": 2,
        "sourceOffset": 9,
    }
    headers = [
        ("x-original-topic", b"stock.ods.daily.v1"),
        ("x-original-partition", b"2"),
        ("x-original-offset", b"9"),
        ("x-error-type", b"unsupported_schema"),
        ("x-error-message", b"unsupported version"),
    ]

    assert MODULE.matches_dead_letter("000001.SZ", b'{"schemaVersion":2}', headers, expected)
    assert not MODULE.matches_dead_letter("000001.SZ", b'{"schemaVersion":1}', headers, expected)


def test_matches_recent_error_by_source_coordinates():
    expected = {
        "key": "000001.SZ",
        "errorType": "unsupported_schema",
        "sourceTopic": "stock.ods.daily.v1",
        "sourcePartition": 2,
        "sourceOffset": 9,
    }
    error = {
        "errorType": "UNSUPPORTED_SCHEMA",
        "message": "unsupported version",
        "topic": "stock.ods.daily.v1",
        "key": "000001.SZ",
        "partition": 2,
        "offset": 9,
    }

    assert MODULE.matches_recent_error(error, expected)
    assert not MODULE.matches_recent_error({**error, "offset": 8}, expected)


def test_delivery_failure_becomes_controlled_runtime_error():
    with pytest.raises(RuntimeError, match="Kafka delivery failed"):
        MODULE.ensure_delivery_complete(
            {"successes": 0, "failures": 1, "errors": ["broker unavailable"]},
            expected_count=1,
        )
