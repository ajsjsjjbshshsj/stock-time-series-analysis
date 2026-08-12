from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class StockDailyEvent:
    event_id: str
    trace_id: str
    ts_code: str
    trade_date: date

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    pre_close: Decimal

    change: Decimal | None
    pct_chg: Decimal | None

    volume: Decimal
    amount: Decimal
    source: str

    event_time: datetime
    ingest_time: datetime
    schema_version: int = 1

    def to_dict(self) -> dict[str, object]:
        """转换为符合 V0.3 Kafka 协议的 camelCase 字典。"""
        return {
            "eventId": self.event_id,
            "traceId": self.trace_id,
            "tsCode": self.ts_code,
            "tradeDate": self.trade_date.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "preClose": self.pre_close,
            "change": self.change,
            "pctChg": self.pct_chg,
            "volume": self.volume,
            "amount": self.amount,
            "source": self.source,
            "eventTime": self.event_time.isoformat(),
            "ingestTime": self.ingest_time.isoformat(),
            "schemaVersion": self.schema_version,
        }