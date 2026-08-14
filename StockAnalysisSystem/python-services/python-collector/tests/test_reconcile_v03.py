import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "reconcile_v03.py"
SPEC = importlib.util.spec_from_file_location("reconcile_v03", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_reconciliation_passes_when_dual_outputs_and_java_match():
    result = MODULE.reconcile(
        collector_count=20,
        mysql_count=20,
        kafka_success=20,
        kafka_failure=0,
        java_success=19,
        java_isolated_failure=1,
        dlt_count=1,
    )

    assert result["status"] == "PASS"
    assert all(check["status"] == "PASS" for check in result["checks"])


def test_reconciliation_reports_partial_when_only_mysql_matches():
    result = MODULE.reconcile(
        collector_count=20,
        mysql_count=20,
        kafka_success=18,
        kafka_failure=2,
        java_success=18,
        java_isolated_failure=0,
        dlt_count=0,
    )

    assert result["status"] == "PARTIAL"


def test_reconciliation_fails_when_mysql_and_kafka_outputs_both_fail():
    result = MODULE.reconcile(
        collector_count=20,
        mysql_count=18,
        kafka_success=0,
        kafka_failure=20,
        java_success=0,
        java_isolated_failure=0,
        dlt_count=0,
    )

    assert result["status"] == "FAIL"


def test_reconciliation_fails_when_mysql_and_kafka_end_to_end_both_fail():
    result = MODULE.reconcile(
        collector_count=20,
        mysql_count=18,
        kafka_success=20,
        kafka_failure=0,
        java_success=19,
        java_isolated_failure=0,
        dlt_count=0,
    )

    assert result["status"] == "FAIL"


def test_smoke_without_mysql_fails_when_java_reconciliation_fails():
    result = MODULE.reconcile(
        collector_count=20,
        mysql_count=None,
        kafka_success=20,
        kafka_failure=0,
        java_success=19,
        java_isolated_failure=0,
        dlt_count=0,
    )

    assert result["status"] == "FAIL"
