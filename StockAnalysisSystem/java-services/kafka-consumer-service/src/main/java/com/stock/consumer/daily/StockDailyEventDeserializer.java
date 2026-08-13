package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.stock.common.model.StockDailyEvent;
import org.springframework.stereotype.Component;

/** Converts the raw Kafka value into the shared V0.3 event model. */
@Component
public final class StockDailyEventDeserializer {

    private final ObjectMapper objectMapper;

    public StockDailyEventDeserializer(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper.copy()
                .registerModule(new JavaTimeModule())
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .disable(DeserializationFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE)
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    public StockDailyEvent deserialize(String payload) throws JsonProcessingException {
        return objectMapper.readValue(payload, StockDailyEvent.class);
    }
}
