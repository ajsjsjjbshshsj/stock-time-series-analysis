from datetime import date, datetime

from app.events.event_factory import EventFactory, SHANGHAI_TZ
from app.models.stock_daily import StockDailyRecord
from app.outputs.output_result import DailyOutputBatch


class DailyOutputBatchFactory:
    """Convert collector dictionaries into one reusable DB/Kafka batch."""

    @staticmethod
    def create(
        records: list[dict],
        source: str,
        business_date: str,
        task_id: int,
    ) -> DailyOutputBatch:
        trace_id = (
            f"daily-{source.strip().lower()}-{business_date}-{task_id}"
        )
        ingest_time = datetime.now(SHANGHAI_TZ)
        enriched_records: list[dict] = []
        events = []

        for raw_record in records:
            # Copy the collector result so output preparation never mutates it.
            record = dict(raw_record)
            record['source'] = source
            model = DailyOutputBatchFactory._to_model(record)
            enriched_records.append(record)
            events.append(
                EventFactory.create_daily_event(
                    record=model,
                    trace_id=trace_id,
                    ingest_time=ingest_time,
                )
            )

        return DailyOutputBatch(records=enriched_records, events=events)

    @staticmethod
    def _to_model(record: dict) -> StockDailyRecord:
        return StockDailyRecord(
            ts_code=record['ts_code'],
            trade_date=DailyOutputBatchFactory._to_date(
                record['trade_date']
            ),
            open=record.get('open'),
            high=record.get('high'),
            low=record.get('low'),
            close=record.get('close'),
            pre_close=record.get('pre_close'),
            change=record.get('change'),
            pct_chg=record.get('pct_chg'),
            vol=record.get('vol'),
            amount=record.get('amount'),
            source=record.get('source'),
        )

    @staticmethod
    def _to_date(value: object) -> date:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            compact = value.replace('-', '')
            return datetime.strptime(compact, '%Y%m%d').date()
        raise ValueError(f"Unsupported trade_date value: {value!r}")
