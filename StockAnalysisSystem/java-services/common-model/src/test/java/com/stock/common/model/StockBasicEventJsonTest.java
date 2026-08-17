package com.stock.common.model;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.Test;

import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StockBasicEventJsonTest {

    private final ObjectMapper objectMapper = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(DeserializationFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE);

    @Test
    void shouldDeserializePythonStockBasicEventFixture() throws Exception {
        StockBasicEvent event = objectMapper.readValue(fixtureJson(), StockBasicEvent.class);

        assertEquals("TUSHARE:000001.SZ:BASIC", event.eventId());
        assertEquals("basic-tushare-20260814-10001", event.traceId());
        assertEquals("000001.SZ", event.tsCode());
        assertEquals("000001", event.symbol());
        assertEquals("平安银行", event.name());
        assertEquals("深圳", event.area());
        assertEquals("银行", event.industry());
        assertEquals(LocalDate.of(1991, 4, 3), event.listDate());
        assertEquals("TUSHARE", event.source());
        assertEquals(OffsetDateTime.parse("2026-08-14T09:00:00+08:00"), event.eventTime());
        assertEquals(OffsetDateTime.parse("2026-08-14T09:00:02+08:00"), event.ingestTime());
        assertEquals(1, event.schemaVersion());
    }

    @Test
    void shouldRejectUnknownJsonField() throws Exception {
        String json = fixtureJson().replace(
                "\"schemaVersion\": 1",
                "\"schemaVersion\": 1, \"unexpectedField\": \"value\"");

        assertThrows(UnrecognizedPropertyException.class,
                () -> objectMapper.readValue(json, StockBasicEvent.class));
    }

    private String fixtureJson() throws Exception {
        return Files.readString(Path.of("..", "..", "docs", "examples", "stock_basic_event_v1.json"));
    }
}
