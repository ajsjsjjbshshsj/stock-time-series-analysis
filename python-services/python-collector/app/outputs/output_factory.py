from app.kafka.producer import StockKafkaProducer
from app.outputs.base_output import BaseOutput
from app.outputs.dual_output import DualOutput
from app.outputs.kafka_output import KafkaOutput
from app.outputs.mysql_output import MysqlOutput
from app.repositories.stock_repository import StockRepository


class OutputFactory:
    """Build the configured output graph and initialize Kafka only when used."""

    @staticmethod
    def create(
        mode: str,
        stock_repository: StockRepository,
        kafka_producer: StockKafkaProducer | None = None,
    ) -> BaseOutput:
        normalized_mode = mode.strip().lower()
        if normalized_mode not in {"mysql", "kafka", "dual"}:
            raise ValueError(
                f"Unsupported output mode: {mode}. Use mysql, kafka, or dual."
            )

        mysql_output = MysqlOutput(stock_repository)

        if normalized_mode == "mysql":
            return mysql_output

        producer = kafka_producer or StockKafkaProducer()
        kafka_output = KafkaOutput(producer)

        if normalized_mode == "kafka":
            return kafka_output
        if normalized_mode == "dual":
            return DualOutput(mysql_output, kafka_output)

        raise AssertionError("validated output mode was not handled")
