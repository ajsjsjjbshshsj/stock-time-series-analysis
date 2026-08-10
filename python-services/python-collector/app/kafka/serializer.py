import simplejson

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

        try:
            payload = simplejson.dumps(
                event.to_dict(),
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