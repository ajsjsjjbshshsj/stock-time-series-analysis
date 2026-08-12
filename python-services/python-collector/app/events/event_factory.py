from datetime import datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from app.events.stock_daily_event import StockDailyEvent
from app.models.stock_daily import StockDailyRecord


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1


class EventFactory:
    @staticmethod
    def create_daily_event(
            record: StockDailyRecord,
            trace_id: str,
            ingest_time: datetime | None = None,
    ) -> StockDailyEvent:
        if not trace_id or not trace_id.strip():
            raise ValueError("trace_id 不能为空")

        if not record.ts_code or not record.ts_code.strip():
            raise ValueError("ts_code 不能为空")

        if record.trade_date is None:
            raise ValueError("trade_date 不能为空")

        if not record.source or not record.source.strip():
            raise ValueError("source 不能为空")

        source = record.source.strip().upper()
        ts_code = record.ts_code.strip().upper()

        event_id = (
            f"{source}:"
            f"{ts_code}:"
            f"{record.trade_date.strftime('%Y%m%d')}"
        )

        event_time = datetime.combine(
            record.trade_date,
            time(hour=15),
            tzinfo=SHANGHAI_TZ,
        )

        actual_ingest_time = EventFactory._normalize_ingest_time(ingest_time)

        return StockDailyEvent(
            event_id=event_id,
            trace_id=trace_id.strip(),
            ts_code=ts_code,
            trade_date=record.trade_date,
            open=EventFactory._required_decimal(record.open, "open"),
            high=EventFactory._required_decimal(record.high, "high"),
            low=EventFactory._required_decimal(record.low, "low"),
            close=EventFactory._required_decimal(record.close, "close"),
            pre_close=EventFactory._required_decimal(
                record.pre_close,
                "pre_close",
            ),
            change=EventFactory._optional_decimal(record.change, "change"),
            pct_chg=EventFactory._optional_decimal(
                record.pct_chg,
                "pct_chg",
            ),
            volume=EventFactory._required_decimal(record.vol, "vol"),
            amount=EventFactory._required_decimal(record.amount, "amount"),
            source=source,
            event_time=event_time,
            ingest_time=actual_ingest_time,
            schema_version=SCHEMA_VERSION,
        )

    @staticmethod
    def _required_decimal(value: object, field_name: str) -> Decimal:
        if value is None:
            raise ValueError(f"{field_name} 不能为空")

        return EventFactory._to_decimal(value, field_name)

    @staticmethod
    def _optional_decimal(
            value: object,
            field_name: str,
    ) -> Decimal | None:
        if value is None:
            return None

        return EventFactory._to_decimal(value, field_name)

    @staticmethod
    def _to_decimal(value: object, field_name: str) -> Decimal:
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name} 不是有效数字: {value}"
            ) from exc

    @staticmethod
    def _normalize_ingest_time(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(SHANGHAI_TZ)

        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ingest_time 必须包含时区")

        return value.astimezone(SHANGHAI_TZ)