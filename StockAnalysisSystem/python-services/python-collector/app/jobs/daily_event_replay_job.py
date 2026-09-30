"""Read-only MySQL daily history replay into the existing Kafka input topic."""

from app.events.event_factory import EventFactory
from app.utils.logger import get_logger


logger = get_logger(__name__)


class ReplayFailure(RuntimeError):
    """Replay stopped; counts include only batches confirmed by flush."""

    def __init__(self, published_count: int, batch_count: int):
        super().__init__('Kafka 历史重放失败')
        self.published_count = published_count
        self.batch_count = batch_count


class DailyEventReplayJob:
    def __init__(self, repository, producer):
        self.repository = repository
        self.producer = producer

    def execute(self, start_date, end_date, batch_size=5000):
        if start_date > end_date:
            raise ValueError('start_date 不能晚于 end_date')
        if batch_size <= 0:
            raise ValueError('batch_size 必须大于 0')

        trace_id = f'replay-{start_date:%Y%m%d}-{end_date:%Y%m%d}'
        published_count = 0
        batch_count = 0
        try:
            for batch in self.repository.iter_daily_records(
                start_date, end_date, batch_size
            ):
                for record in batch:
                    try:
                        event = EventFactory.create_daily_event(
                            record, trace_id=trace_id
                        )
                        self.producer.send_event(event)
                    except Exception:
                        # Settle earlier accepted records, but keep the record
                        # error primary and do not count this incomplete batch.
                        try:
                            self.producer.flush()
                        except Exception as settle_exc:
                            logger.warning(
                                'Kafka replay partial-batch settlement failed: %s',
                                type(settle_exc).__name__,
                            )
                        raise
                self.producer.flush()
                published_count += len(batch)
                batch_count += 1
        except Exception as exc:
            raise ReplayFailure(published_count, batch_count) from exc

        return {
            'success': True,
            'published_count': published_count,
            'batch_count': batch_count,
        }
