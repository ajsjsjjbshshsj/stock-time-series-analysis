from app.kafka.producer import KafkaProducerError, StockKafkaProducer
from app.outputs.base_output import BaseOutput
from app.outputs.output_result import DailyOutputBatch, OutputResult


class KafkaOutput(BaseOutput):
    OUTPUT_TYPE = "KAFKA"

    def __init__(self, producer: StockKafkaProducer):
        self.producer = producer

    def deliver(self, batch: DailyOutputBatch) -> OutputResult:
        events = batch.events
        expected_count = len(events)

        if expected_count == 0:
            return OutputResult(
                output_type=self.OUTPUT_TYPE,
                expected_count=0,
                success_count=0,
                failure_count=0,
                errors=[],
            )

        before = self.producer.statistics
        before_error_count = len(before["errors"])
        errors: list[str] = []

        for event in events:
            try:
                self.producer.send_event(event)
            except KafkaProducerError as exc:
                errors.append(str(exc))

        try:
            self.producer.flush()
        except KafkaProducerError as exc:
            errors.append(str(exc))

        after = self.producer.statistics
        success_count = max(
            0,
            int(after["successCount"]) - int(before["successCount"]),
        )
        success_count = min(success_count, expected_count)
        failure_count = expected_count - success_count

        errors.extend(after["errors"][before_error_count:])
        unique_errors = list(dict.fromkeys(errors))

        if failure_count > 0 and not unique_errors:
            unique_errors.append(
                f"{failure_count} Kafka message(s) were not delivered"
            )

        return OutputResult(
            output_type=self.OUTPUT_TYPE,
            expected_count=expected_count,
            success_count=success_count,
            failure_count=failure_count,
            errors=unique_errors,
        )
