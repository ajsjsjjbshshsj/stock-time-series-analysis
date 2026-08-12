from unittest.mock import MagicMock, patch

import pytest

from app.config import normalize_output_mode
from app.outputs.dual_output import DualOutput
from app.outputs.kafka_output import KafkaOutput
from app.outputs.mysql_output import MysqlOutput
from app.outputs.output_factory import OutputFactory


def test_output_factory_creates_mysql_without_starting_kafka():
    repository = MagicMock()

    with patch("app.outputs.output_factory.StockKafkaProducer") as producer:
        output = OutputFactory.create("mysql", repository)

    assert isinstance(output, MysqlOutput)
    producer.assert_not_called()


def test_output_factory_creates_kafka_with_supplied_producer():
    producer = MagicMock()

    output = OutputFactory.create("KAFKA", MagicMock(), producer)

    assert isinstance(output, KafkaOutput)
    assert output.producer is producer


def test_output_factory_creates_dual_output():
    output = OutputFactory.create("dual", MagicMock(), MagicMock())

    assert isinstance(output, DualOutput)
    assert isinstance(output.mysql_output, MysqlOutput)
    assert isinstance(output.kafka_output, KafkaOutput)


def test_output_factory_rejects_unknown_mode_before_use():
    with pytest.raises(ValueError, match="Unsupported output mode"):
        OutputFactory.create("file", MagicMock(), MagicMock())


def test_normalize_output_mode_is_case_insensitive():
    assert normalize_output_mode(" Dual ") == "dual"


def test_normalize_output_mode_rejects_unknown_value():
    with pytest.raises(ValueError, match="COLLECTOR_OUTPUT_MODE"):
        normalize_output_mode("file")
