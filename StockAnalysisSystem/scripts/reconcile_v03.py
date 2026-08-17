#!/usr/bin/env python3
"""Reconcile one controlled V0.3 Collector/MySQL/Kafka/Java batch."""

import argparse
import json
import sys


def check(name, expected, actual, exercised=True):
    if not exercised:
        return {"name": name, "status": "NOT_EXERCISED", "expected": expected, "actual": actual}
    return {
        "name": name,
        "status": "PASS" if expected == actual else "FAIL",
        "expected": expected,
        "actual": actual,
    }


def reconcile(
    collector_count,
    mysql_count,
    kafka_success,
    kafka_failure,
    java_success,
    java_isolated_failure,
    dlt_count,
):
    checks = [
        check("MySQL rows equal Collector rows", collector_count, mysql_count, mysql_count is not None),
        check("Kafka callbacks account for Collector rows", collector_count, kafka_success + kafka_failure),
        check("Kafka delivery failures", 0, kafka_failure),
        check("Kafka success equals Java success plus isolated failures", kafka_success, java_success + java_isolated_failure),
        check("DLT records equal Java isolated failures", java_isolated_failure, dlt_count),
    ]
    mysql_status = checks[0]["status"]
    kafka_status = "PASS" if all(item["status"] == "PASS" for item in checks[1:]) else "FAIL"
    if mysql_status == "NOT_EXERCISED":
        status = kafka_status
    elif mysql_status == kafka_status == "PASS":
        status = "PASS"
    elif mysql_status == kafka_status == "FAIL":
        status = "FAIL"
    else:
        status = "PARTIAL"
    return {"status": status, "checks": checks}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace-id", required=True)
    parser.add_argument("--collector-count", type=int, required=True)
    parser.add_argument("--mysql-count", type=int)
    parser.add_argument("--kafka-success", type=int, required=True)
    parser.add_argument("--kafka-failure", type=int, required=True)
    parser.add_argument("--java-success", type=int, required=True)
    parser.add_argument("--java-isolated-failure", type=int, required=True)
    parser.add_argument("--dlt-count", type=int, required=True)
    parser.add_argument("--real-collector", action="store_true")
    args = parser.parse_args(argv)

    if args.real_collector and args.mysql_count is None:
        parser.error("--real-collector requires --mysql-count")

    result = reconcile(
        args.collector_count,
        args.mysql_count,
        args.kafka_success,
        args.kafka_failure,
        args.java_success,
        args.java_isolated_failure,
        args.dlt_count,
    )
    result.update({
        "traceId": args.trace_id,
        "mode": "real-collector" if args.real_collector else "deterministic-smoke",
        "realCollectorMysqlReconciliation": "EXERCISED" if args.real_collector else "NOT_EXERCISED",
        "rollbackMode": "COLLECTOR_OUTPUT_MODE=mysql",
    })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
