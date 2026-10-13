package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.stock.common.model.StockDailyEvent;
import com.stock.common.validation.StockDailyEventCodec;
import org.springframework.stereotype.Component;

/** Converts the raw Kafka value into the shared V0.3 event model. */
@Component
public final class StockDailyEventDeserializer {

    private final StockDailyEventCodec codec;

    public StockDailyEventDeserializer(ObjectMapper objectMapper) {
        this.codec = new StockDailyEventCodec(objectMapper);
    }

    public StockDailyEvent deserialize(String payload) throws JsonProcessingException {
        return codec.decode(payload);
    }
}
