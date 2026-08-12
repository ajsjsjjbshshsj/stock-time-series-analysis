from datetime import date

from app.events.event_factory import EventFactory
from app.kafka.producer import KafkaProducerError
from app.models.stock_daily import StockDailyRecord
from app.outputs.kafka_output import KafkaOutput
from app.outputs.output_result import DailyOutputBatch


def create_event(ts_code: str):
    record = StockDailyRecord(
        ts_code=ts_code,
        trade_date=date(2026, 8, 7),
        open=10.0,
        high=10.5,
        low=9.8,
        close=10.2,
        pre_close=10.0,
        change=0.2,
        pct_chg=2.0,
        vol=100000.0,
        amount=1020000.0,
        source="tushare",
    )
    return EventFactory.create_daily_event(
        record=record,
        trace_id="daily-tushare-20260807-10001",
    )


class FakeProducer:
    def __init__(
        self,
        outcomes=None,
        initial_success=0,
        initial_failure=0,
        initial_errors=None,
        flush_timeout=False,
    ):
        self.outcomes = list(outcomes or [])
        self.sent_events = []
        self.flush_count = 0
        self.flush_timeout = flush_timeout
        self._success_count = initial_success
        self._failure_count = initial_failure
        self._errors = list(initial_errors or [])
        self._pending = []

    @property
    def statistics(self):
        return {
            "successCount": self._success_count,
            "failureCount": self._failure_count,
            "errors": list(self._errors),
        }

    def send_event(self, event):
        self.sent_events.append(event)
        outcome = self.outcomes.pop(0) if self.outcomes else "success"
        if outcome == "enqueue_failure":
            self._failure_count += 1
            self._errors.append("local queue full")
            raise KafkaProducerError("local queue full")
        self._pending.append(outcome)

    def flush(self):
        self.flush_count += 1
        if self.flush_timeout:
            raise KafkaProducerError("messages remained undelivered")

        delivery_errors = []
        for outcome in self._pending:
            if outcome == "success":
                self._success_count += 1
            else:
                self._failure_count += 1
                self._errors.append("delivery failed")
                delivery_errors.append("delivery failed")
        self._pending.clear()

        if delivery_errors:
            raise KafkaProducerError(delivery_errors[-1])
        return self.statistics


def create_batch(count: int):
    events = [create_event(f"00000{index}.SZ") for index in range(1, count + 1)]
    return DailyOutputBatch(records=[], events=events)


def test_kafka_output_handles_empty_batch():
    producer = FakeProducer()
    result = KafkaOutput(producer).deliver(create_batch(0))

    assert producer.sent_events == []
    assert producer.flush_count == 0
    assert result.success is True
    assert result.expected_count == 0


def test_kafka_output_reports_only_current_batch_success():
    producer = FakeProducer(
        outcomes=["success", "success"],
        initial_success=100,
        initial_failure=5,
        initial_errors=["historical failure"],
    )

    result = KafkaOutput(producer).deliver(create_batch(2))

    assert result.expected_count == 2
    assert result.success_count == 2
    assert result.failure_count == 0
    assert result.errors == []
    assert producer.flush_count == 1


def test_kafka_output_continues_after_enqueue_failure():
    producer = FakeProducer(
        outcomes=["success", "enqueue_failure", "success"],
    )

    result = KafkaOutput(producer).deliver(create_batch(3))

    assert len(producer.sent_events) == 3
    assert result.success_count == 2
    assert result.failure_count == 1
    assert result.errors == ["local queue full"]


def test_kafka_output_reports_delivery_callback_failure():
    producer = FakeProducer(outcomes=["success", "delivery_failure"])

    result = KafkaOutput(producer).deliver(create_batch(2))

    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.errors == ["delivery failed"]
    assert result.success is False


def test_kafka_output_reports_flush_timeout_as_batch_failure():
    producer = FakeProducer(outcomes=["success", "success"], flush_timeout=True)

    result = KafkaOutput(producer).deliver(create_batch(2))

    assert result.success_count == 0
    assert result.failure_count == 2
    assert result.errors == ["messages remained undelivered"]
