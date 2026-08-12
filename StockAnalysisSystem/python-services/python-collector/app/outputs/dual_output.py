from app.outputs.base_output import BaseOutput
from app.outputs.output_result import DailyOutputBatch, OutputResult


class DualOutput(BaseOutput):
    """Deliver one batch to MySQL and Kafka without short-circuiting."""

    OUTPUT_TYPE = "DUAL"

    def __init__(self, mysql_output: BaseOutput, kafka_output: BaseOutput):
        self.mysql_output = mysql_output
        self.kafka_output = kafka_output

    def deliver(self, batch: DailyOutputBatch) -> OutputResult:
        mysql_result = self._deliver_safely("MYSQL", self.mysql_output, batch)
        kafka_result = self._deliver_safely("KAFKA", self.kafka_output, batch)
        expected_count = max(
            mysql_result.expected_count,
            kafka_result.expected_count,
        )

        # A record is fully successful only when both destinations accepted it.
        success_count = min(
            mysql_result.success_count,
            kafka_result.success_count,
        )
        errors = [
            f"{output_type}: {error}"
            for output_type, result in (
                ("MYSQL", mysql_result),
                ("KAFKA", kafka_result),
            )
            for error in result.errors
        ]

        return OutputResult(
            output_type=self.OUTPUT_TYPE,
            expected_count=expected_count,
            success_count=success_count,
            failure_count=expected_count - success_count,
            errors=errors,
            details={
                "MYSQL": mysql_result,
                "KAFKA": kafka_result,
            },
        )

    @staticmethod
    def _deliver_safely(
        output_type: str,
        output: BaseOutput,
        batch: DailyOutputBatch,
    ) -> OutputResult:
        """Unexpected adapter errors must not prevent the other output."""
        try:
            return output.deliver(batch)
        except Exception as exc:
            expected_count = (
                len(batch.events)
                if output_type == "KAFKA"
                else len(batch.records)
            )
            return OutputResult(
                output_type=output_type,
                expected_count=expected_count,
                success_count=0,
                failure_count=expected_count,
                errors=[str(exc)],
            )
