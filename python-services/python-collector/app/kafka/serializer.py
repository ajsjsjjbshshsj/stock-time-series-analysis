import simplejson
from decimal import Decimal

from app.events.stock_daily_event import StockDailyEvent


class KafkaSerializationError(ValueError):
    """Kafka 消息序列化失败。"""


class KafkaJsonSerializer:
    """将股票事件转换为 UTF-8 JSON 字节。"""

    @staticmethod
    def serialize(event: StockDailyEvent) -> bytes:
        if not isinstance(event, StockDailyEvent):
            raise KafkaSerializationError(
                "event 必须是 StockDailyEvent"
            )

        payload_data = event.to_dict()
        KafkaJsonSerializer._validate_json_numbers(payload_data)

        try:
            payload = simplejson.dumps(
                payload_data,
                ensure_ascii=False,
                use_decimal=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise KafkaSerializationError(
                f"StockDailyEvent 序列化失败: {exc}"
            ) from exc

        return payload.encode("utf-8")

    @staticmethod
    def _validate_json_numbers(value: object) -> None:
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise KafkaSerializationError(
                    f"JSON numbers must be finite Decimal values: {value}"
                )
            return

        if isinstance(value, dict):
            for nested_value in value.values():
                KafkaJsonSerializer._validate_json_numbers(nested_value)
            return

        if isinstance(value, (list, tuple)):
            for nested_value in value:
                KafkaJsonSerializer._validate_json_numbers(nested_value)
