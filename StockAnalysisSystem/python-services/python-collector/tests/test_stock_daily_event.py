from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.events.event_factory import EventFactory
from app.models.stock_daily import StockDailyRecord


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def create_record() -> StockDailyRecord:
    return StockDailyRecord(
        ts_code="000001.SZ",
        trade_date=date(2026, 7, 3),
        open=10.25,
        high=10.68,
        low=10.12,
        close=10.55,
        pre_close=10.20,
        change=0.35,
        pct_chg=3.4314,
        vol=1250345.00,
        amount=13054890.25,
        source="tushare",
    )


def test_event_id_is_stable():
    record = create_record()
    ingest_time = datetime(2026, 7, 3, 16, 20, 30, tzinfo=SHANGHAI_TZ)

    first = EventFactory.create_daily_event(
        record=record,
        trace_id="daily-tushare-20260703-10001",
        ingest_time=ingest_time,
    )

    second = EventFactory.create_daily_event(
        record=record,
        trace_id="daily-tushare-20260703-10001",
        ingest_time=ingest_time,
    )

    assert first.event_id == second.event_id
    assert first.event_id == "TUSHARE:000001.SZ:20260703"


def test_event_uses_protocol_types():
    event = EventFactory.create_daily_event(
        record=create_record(),
        trace_id="daily-tushare-20260703-10001",
    )

    assert event.trade_date == date(2026, 7, 3)
    assert event.close == Decimal("10.55")
    assert event.volume == Decimal("1250345.0")
    assert event.amount == Decimal("13054890.25")
    assert event.schema_version == 1
    assert event.source == "TUSHARE"


def test_to_dict_uses_camel_case():
    event = EventFactory.create_daily_event(
        record=create_record(),
        trace_id="daily-tushare-20260703-10001",
    )

    payload = event.to_dict()

    assert payload["eventId"] == "TUSHARE:000001.SZ:20260703"
    assert payload["traceId"] == "daily-tushare-20260703-10001"
    assert payload["tsCode"] == "000001.SZ"
    assert payload["tradeDate"] == "2026-07-03"
    assert payload["preClose"] == Decimal("10.2")
    assert payload["pctChg"] == Decimal("3.4314")
    assert payload["schemaVersion"] == 1

    assert "event_id" not in payload
    assert "trace_id" not in payload
    assert "ts_code" not in payload
    assert "trade_date" not in payload
    assert "schema_version" not in payload


def test_event_time_uses_shanghai_timezone():
    event = EventFactory.create_daily_event(
        record=create_record(),
        trace_id="daily-tushare-20260703-10001",
    )

    assert event.event_time.isoformat() == "2026-07-03T15:00:00+08:00"
    assert event.ingest_time.utcoffset().total_seconds() == 8 * 60 * 60


def test_factory_rejects_missing_required_price():
    record = create_record()
    record.close = None

    with pytest.raises(ValueError, match="close"):
        EventFactory.create_daily_event(
            record=record,
            trace_id="daily-tushare-20260703-10001",
        )


def test_factory_rejects_empty_trace_id():
    with pytest.raises(ValueError, match="trace_id"):
        EventFactory.create_daily_event(
            record=create_record(),
            trace_id="",
        )