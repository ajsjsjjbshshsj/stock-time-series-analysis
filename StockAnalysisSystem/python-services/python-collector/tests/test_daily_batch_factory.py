from datetime import date

from app.outputs.daily_batch_factory import DailyOutputBatchFactory


def _record(ts_code="000001.SZ", trade_date=date(2026, 8, 7)):
    return {
        "ts_code": ts_code,
        "trade_date": trade_date,
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


def test_batch_factory_creates_matching_records_and_events():
    records = [_record(), _record("000002.SZ", "20260807")]

    batch = DailyOutputBatchFactory.create(
        records=records,
        source="tushare",
        business_date="20260807",
        task_id=42,
    )

    assert len(batch.records) == 2
    assert len(batch.events) == 2
    assert {event.ts_code for event in batch.events} == {
        "000001.SZ",
        "000002.SZ",
    }
    assert {event.trace_id for event in batch.events} == {
        "daily-tushare-20260807-42"
    }
    assert all(record["source"] == "tushare" for record in batch.records)


def test_batch_factory_does_not_mutate_collector_records():
    original = _record()

    DailyOutputBatchFactory.create(
        records=[original],
        source="tushare",
        business_date="20260807",
        task_id=42,
    )

    assert "source" not in original


def test_batch_factory_uses_stable_business_event_id():
    first = DailyOutputBatchFactory.create(
        [_record()], "tushare", "20260807", 1
    )
    second = DailyOutputBatchFactory.create(
        [_record()], "tushare", "20260807", 2
    )

    assert first.events[0].event_id == second.events[0].event_id
    assert first.events[0].trace_id != second.events[0].trace_id
