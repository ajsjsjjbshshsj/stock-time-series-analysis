import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.events.event_factory import EventFactory
from app.kafka.serializer import (
    KafkaJsonSerializer,
    KafkaSerializationError,
)
from app.models.stock_daily import StockDailyRecord


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def create_event(
    change: float | None = 0.35,
    pct_chg: float | None = 3.4314,
):
    record = StockDailyRecord(
        ts_code="000001.SZ",
        trade_date=date(2026, 7, 3),
        open=10.25,
        high=10.68,
        low=10.12,
        close=10.55,
        pre_close=10.20,
        change=change,
        pct_chg=pct_chg,
        vol=1250345.00,
        amount=13054890.25,
        source="tushare",
    )

    return EventFactory.create_daily_event(
        record=record,
        trace_id="daily-tushare-20260703-10001",
        ingest_time=datetime(
            2026,
            7,
            3,
            16,
            20,
            30,
            tzinfo=SHANGHAI_TZ,
        ),
    )


def decode_payload(value: bytes) -> dict:
    return json.loads(
        value.decode("utf-8"),
        parse_float=Decimal,
    )


def test_serializer_returns_utf8_bytes():
    result = KafkaJsonSerializer.serialize(create_event())

    assert isinstance(result, bytes)

    decoded_text = result.decode("utf-8")

    assert decoded_text.startswith("{")
    assert decoded_text.endswith("}")


def test_serializer_preserves_protocol_fields():
    result = KafkaJsonSerializer.serialize(create_event())
    payload = decode_payload(result)

    assert payload["eventId"] == "TUSHARE:000001.SZ:20260703"
    assert payload["traceId"] == "daily-tushare-20260703-10001"
    assert payload["tsCode"] == "000001.SZ"
    assert payload["tradeDate"] == "2026-07-03"
    assert payload["source"] == "TUSHARE"
    assert payload["schemaVersion"] == 1

    assert payload["eventTime"] == "2026-07-03T15:00:00+08:00"
    assert payload["ingestTime"] == "2026-07-03T16:20:30+08:00"


def test_serializer_outputs_decimal_as_json_number():
    result = KafkaJsonSerializer.serialize(create_event())
    payload = decode_payload(result)

    assert payload["open"] == Decimal("10.25")
    assert payload["close"] == Decimal("10.55")
    assert payload["preClose"] == Decimal("10.2")
    assert payload["pctChg"] == Decimal("3.4314")
    assert payload["volume"] == Decimal("1250345.0")
    assert payload["amount"] == Decimal("13054890.25")

    assert not isinstance(payload["close"], str)
    assert not isinstance(payload["amount"], str)


def test_serializer_keeps_optional_values_as_null():
    event = create_event(change=None, pct_chg=None)

    result = KafkaJsonSerializer.serialize(event)
    payload = decode_payload(result)

    assert payload["change"] is None
    assert payload["pctChg"] is None


def test_serializer_keeps_camel_case_fields():
    result = KafkaJsonSerializer.serialize(create_event())
    payload = decode_payload(result)

    assert "eventId" in payload
    assert "tsCode" in payload
    assert "tradeDate" in payload
    assert "schemaVersion" in payload

    assert "event_id" not in payload
    assert "ts_code" not in payload
    assert "trade_date" not in payload
    assert "schema_version" not in payload


def test_serializer_rejects_invalid_input():
    with pytest.raises(
        KafkaSerializationError,
        match="StockDailyEvent",
    ):
        KafkaJsonSerializer.serialize({"tsCode": "000001.SZ"})


@pytest.mark.parametrize("invalid_value", [Decimal("NaN"), Decimal("Infinity")])
def test_serializer_rejects_non_finite_decimal(invalid_value):
    event = replace(create_event(), close=invalid_value)

    with pytest.raises(KafkaSerializationError, match="finite Decimal"):
        KafkaJsonSerializer.serialize(event)
