from collections.abc import Mapping

try:
    from confluent_kafka import Producer as ConfluentProducer
except ImportError:  # pragma: no cover - exercised only without dependency
    ConfluentProducer = None

from app.config import KAFKA_CONFIG, KAFKA_TOPICS
from app.events.stock_daily_event import StockDailyEvent
from app.kafka.delivery_callback import DeliveryCallback
from app.kafka.serializer import KafkaJsonSerializer


class KafkaProducerError(RuntimeError):
    """Kafka producer operation failed."""


class StockKafkaProducer:
    """Asynchronous producer for V0.3 stock events."""

    def __init__(
        self,
        client=None,
        producer_config: Mapping[str, object] | None = None,
        daily_topic: str | None = None,
    ):
        config = dict(producer_config or KAFKA_CONFIG)
        self._daily_topic = daily_topic or KAFKA_TOPICS['daily']
        self._delivery_callback = DeliveryCallback()

        if client is not None:
            self._client = client
        elif ConfluentProducer is not None:
            self._client = ConfluentProducer(config)
        else:
            raise KafkaProducerError(
                'confluent-kafka is not installed; install project requirements'
            )

    def send_event(
        self,
        event: StockDailyEvent,
        topic: str | None = None,
    ) -> None:
        try:
            self._client.produce(
                topic=topic or self._daily_topic,
                key=event.ts_code,
                value=KafkaJsonSerializer.serialize(event),
                on_delivery=self._delivery_callback,
            )
            self._client.poll(0)
        except Exception as exc:
            self._delivery_callback.record_failure(exc)
            raise KafkaProducerError(
                f'Kafka message enqueue failed: {exc}'
            ) from exc

    def flush(self, timeout: float | None = None) -> dict[str, object]:
        if timeout is None:
            remaining = self._client.flush()
        else:
            remaining = self._client.flush(timeout)

        if remaining:
            raise KafkaProducerError(
                f'{remaining} Kafka message(s) remained undelivered after flush'
            )

        statistics = self.statistics
        if statistics['failureCount']:
            errors = statistics['errors']
            last_error = errors[-1] if errors else 'unknown delivery error'
            raise KafkaProducerError(f'Kafka delivery failed: {last_error}')

        return statistics

    def close(self) -> dict[str, object]:
        return self.flush()

    @property
    def statistics(self) -> dict[str, object]:
        return self._delivery_callback.statistics
