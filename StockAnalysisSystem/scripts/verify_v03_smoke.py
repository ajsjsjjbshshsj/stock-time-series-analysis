#!/usr/bin/env python3
"""Verify the V0.3 Python -> Kafka -> Java path with deterministic messages."""

import argparse
import json
import sys
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen


STAT_FIELDS = (
    "totalConsumed",
    "successCount",
    "failureCount",
    "jsonParseFailureCount",
    "validationFailureCount",
    "unsupportedSchemaCount",
)


def event_template(trace_id):
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return {
        "eventId": "SMOKE:000001.SZ:20260814:0",
        "traceId": trace_id,
        "tsCode": "000001.SZ",
        "tradeDate": "2026-08-14",
        "open": 10.25,
        "high": 10.68,
        "low": 10.12,
        "close": 10.55,
        "preClose": 10.20,
        "change": 0.35,
        "pctChg": 3.4314,
        "volume": 1250345.00,
        "amount": 13054890.25,
        "source": "SMOKE",
        "eventTime": now,
        "ingestTime": now,
        "schemaVersion": 1,
    }


def build_messages(scenario, count, trace_id):
    if count < 1:
        raise ValueError("count must be greater than zero")
    messages = []
    if scenario in {"valid", "all"}:
        for index in range(count):
            payload = deepcopy(event_template(trace_id))
            payload["eventId"] = f"SMOKE:000001.SZ:20260814:{index}"
            messages.append({"kind": "valid", "key": payload["tsCode"], "payload": payload})
    if scenario in {"malformed", "all"}:
        messages.append({"kind": "malformed", "key": "000003.SZ", "raw": b'{"eventId":'})
    if scenario in {"key-mismatch", "all"}:
        payload = event_template(trace_id)
        messages.append({"kind": "key-mismatch", "key": "600000.SH", "payload": payload})
    if scenario in {"unsupported-schema", "all"}:
        payload = event_template(trace_id)
        payload["schemaVersion"] = 2
        messages.append({"kind": "unsupported-schema", "key": payload["tsCode"], "payload": payload})
    return messages


def calculate_deltas(baseline, current):
    return {
        field: int(current.get(field, 0)) - int(baseline.get(field, 0))
        for field in baseline
        if field in current and isinstance(baseline[field], (int, float))
    }


def matches_dead_letter(key, value, headers, expected):
    header_map = {
        name: payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
        for name, payload in (headers or [])
    }
    return (
        key == expected["key"]
        and value == expected["raw"]
        and header_map.get("x-original-topic") == expected["sourceTopic"]
        and header_map.get("x-original-partition") == str(expected["sourcePartition"])
        and header_map.get("x-original-offset") == str(expected["sourceOffset"])
        and header_map.get("x-error-type") == expected["errorType"]
        and bool(header_map.get("x-error-message"))
    )


def matches_recent_error(error, expected):
    return (
        error.get("errorType") == expected["errorType"].upper()
        and bool(error.get("message"))
        and error.get("topic") == expected["sourceTopic"]
        and error.get("key") == expected["key"]
        and error.get("partition") == expected["sourcePartition"]
        and error.get("offset") == expected["sourceOffset"]
    )


def ensure_delivery_complete(delivery, expected_count):
    if delivery["failures"]:
        detail = delivery.get("errors") or ["unknown delivery error"]
        raise RuntimeError(f"Kafka delivery failed: {detail[-1]}")
    if delivery["successes"] != expected_count:
        raise RuntimeError(
            f"Kafka delivery count mismatch: expected {expected_count}, got {delivery['successes']}"
        )


def collect_dead_letters(consumer, expected_records, timeout):
    matches = []
    remaining = list(expected_records)
    deadline = time.monotonic() + timeout
    while remaining and time.monotonic() < deadline:
        message = consumer.poll(0.25)
        if message is None or message.error():
            continue
        key = message.key().decode("utf-8", errors="replace") if message.key() else ""
        value = message.value() or b""
        expected = next((item for item in remaining if matches_dead_letter(key, value, message.headers(), item)), None)
        if expected is not None:
            evidence = {
                "key": key,
                "partition": message.partition(),
                "offset": message.offset(),
                "valueMatchesSource": True,
                "headers": {
                    name: payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
                    for name, payload in (message.headers() or [])
                },
            }
            matches.append(evidence)
            remaining.remove(expected)
    return matches


def get_json(url, timeout):
    request = Request(url, headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


def expected_deltas(scenario, count):
    expected = {field: 0 for field in STAT_FIELDS}
    if scenario in {"valid", "all"}:
        expected["totalConsumed"] += count
        expected["successCount"] += count
    failures = {
        "malformed": "jsonParseFailureCount",
        "key-mismatch": "validationFailureCount",
        "unsupported-schema": "unsupportedSchemaCount",
    }
    selected = failures if scenario == "all" else {scenario: failures[scenario]} if scenario in failures else {}
    for subtype in selected.values():
        expected["totalConsumed"] += 1
        expected["failureCount"] += 1
        expected[subtype] += 1
    return expected


def wait_for_deltas(base_url, baseline, expected, timeout):
    deadline = time.monotonic() + timeout
    current = baseline
    while time.monotonic() < deadline:
        current = get_json(f"{base_url}/api/consumer/statistics", min(timeout, 5))
        deltas = calculate_deltas(baseline, current)
        if all(deltas.get(field, 0) >= value for field, value in expected.items()):
            return current, deltas
        time.sleep(0.25)
    return current, calculate_deltas(baseline, current)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--scenario", choices=("valid", "malformed", "key-mismatch", "unsupported-schema", "all"), default="valid")
    parser.add_argument("--java-base-url", default="http://localhost:8080")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--topic", default="stock.ods.daily.v1")
    parser.add_argument("--dlt-topic", default="stock.dead-letter.v1")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)
    started = time.monotonic()
    result = {"status": "FAIL", "scenario": args.scenario, "count": args.count}

    try:
        from confluent_kafka import Consumer, Producer, TopicPartition

        health = get_json(f"{args.java_base_url.rstrip('/')}/actuator/health", min(args.timeout, 5))
        if health.get("status") != "UP":
            raise RuntimeError(f"Java health is not UP: {health.get('status')}")
        base_url = args.java_base_url.rstrip("/")
        baseline = get_json(f"{base_url}/api/consumer/statistics", min(args.timeout, 5))
        trace_id = f"v03-smoke-{uuid.uuid4().hex[:12]}"
        messages = build_messages(args.scenario, args.count, trace_id)
        expected = expected_deltas(args.scenario, args.count)
        expected_failure_count = expected["failureCount"]
        dlt_error_type_by_scenario = {
            "malformed": "deserialization",
            "key-mismatch": "validation",
            "unsupported-schema": "unsupported_schema",
        }
        dlt_consumer = None
        if expected_failure_count:
            dlt_consumer = Consumer({
                "bootstrap.servers": args.bootstrap_servers,
                "group.id": f"v03-smoke-dlt-{uuid.uuid4().hex}",
                "auto.offset.reset": "latest",
                "enable.auto.commit": False,
            })
            dlt_consumer.subscribe([args.dlt_topic])
            assignment_deadline = time.monotonic() + min(args.timeout, 5)
            while not dlt_consumer.assignment() and time.monotonic() < assignment_deadline:
                dlt_consumer.poll(0.1)
            assignment = dlt_consumer.assignment()
            if not assignment:
                raise RuntimeError("DLT consumer did not receive a partition assignment")
            for partition in assignment:
                _, high = dlt_consumer.get_watermark_offsets(partition, timeout=min(args.timeout, 5))
                dlt_consumer.seek(TopicPartition(partition.topic, partition.partition, high))
        delivery = {"successes": 0, "failures": 0, "errors": [], "records": []}

        def delivered(error, kafka_message, source):
            if error is None:
                delivery["successes"] += 1
                source["sourceTopic"] = kafka_message.topic()
                source["sourcePartition"] = kafka_message.partition()
                source["sourceOffset"] = kafka_message.offset()
                delivery["records"].append({
                    "kind": source["kind"],
                    "key": source["key"],
                    "topic": kafka_message.topic(),
                    "partition": kafka_message.partition(),
                    "offset": kafka_message.offset(),
                })
            else:
                delivery["failures"] += 1
                delivery["errors"].append(str(error))

        producer = Producer({"bootstrap.servers": args.bootstrap_servers, "client.id": "v03-smoke-verifier"})
        for message in messages:
            raw = message.get("raw")
            value = raw if raw is not None else json.dumps(message["payload"], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            message["raw"] = value
            producer.produce(
                args.topic,
                key=message["key"].encode("utf-8"),
                value=value,
                on_delivery=lambda error, kafka_message, source=message: delivered(error, kafka_message, source),
            )
            producer.poll(0)
        remaining = producer.flush(args.timeout)
        if remaining:
            raise RuntimeError(f"{remaining} Kafka message(s) remained undelivered")
        ensure_delivery_complete(delivery, len(messages))

        expected_dead_letters = [
            {
                "key": message["key"],
                "raw": message["raw"],
                "errorType": dlt_error_type_by_scenario[message["kind"]],
                "sourceTopic": message["sourceTopic"],
                "sourcePartition": message["sourcePartition"],
                "sourceOffset": message["sourceOffset"],
            }
            for message in messages
            if message["kind"] != "valid"
        ]

        current, deltas = wait_for_deltas(base_url, baseline, expected, args.timeout)
        recent_errors = get_json(f"{base_url}/api/consumer/errors", min(args.timeout, 5))
        health_after = get_json(f"{base_url}/actuator/health", min(args.timeout, 5))
        dead_letters = []
        if dlt_consumer is not None:
            dead_letters = collect_dead_letters(
                dlt_consumer,
                expected_dead_letters,
                args.timeout,
            )
            dlt_consumer.close()
        passed = delivery["failures"] == 0 and delivery["successes"] == len(messages)
        passed = passed and all(deltas.get(field, 0) == value for field, value in expected.items())
        passed = passed and health_after.get("status") == "UP"
        if expected["failureCount"]:
            passed = passed and all(
                any(matches_recent_error(error, expected_record) for error in recent_errors)
                for expected_record in expected_dead_letters
            )
            passed = passed and len(dead_letters) == expected_failure_count
        result.update({
            "status": "PASS" if passed else "FAIL",
            "traceId": trace_id,
            "healthBefore": health.get("status"),
            "healthAfter": health_after.get("status"),
            "producer": delivery,
            "expectedDeltas": expected,
            "javaDeltas": {field: deltas.get(field, 0) for field in STAT_FIELDS},
            "deadLetterMatches": dead_letters,
            "baseline": baseline,
            "current": current,
        })
    except (ImportError, OSError, RuntimeError, URLError, ValueError) as error:
        result["error"] = str(error)

    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
