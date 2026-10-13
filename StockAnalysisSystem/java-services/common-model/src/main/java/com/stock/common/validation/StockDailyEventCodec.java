package com.stock.common.validation;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonMappingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.stock.common.model.StockDailyEvent;

/** Strict Jackson 2 reader shared by Consumer and Flink; never mutates an injected mapper. */
public final class StockDailyEventCodec {
    private final ObjectMapper reader;

    public StockDailyEventCodec() {
        this(new ObjectMapper());
    }

    public StockDailyEventCodec(ObjectMapper base) {
        this.reader = base.copy().registerModule(new JavaTimeModule())
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .disable(DeserializationFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE);
    }

    public StockDailyEvent decode(String payload) throws JsonProcessingException {
        if (payload == null) throw missingObject();
        StockDailyEvent event = reader.readValue(payload, StockDailyEvent.class);
        if (event == null) throw missingObject();
        return event;
    }

    private static JsonMappingException missingObject() {
        return JsonMappingException.from((JsonParser) null, "StockDailyEvent payload must be an object");
    }
}
