package com.stock.common.model;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.exc.UnrecognizedPropertyException;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StockDailyEventJsonTest {

    private final ObjectMapper objectMapper = new ObjectMapper()
            .registerModule(new JavaTimeModule())
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            // Keep the explicit +08:00 offset from the cross-language contract.
            .disable(DeserializationFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE);

    @Test
    void shouldDeserializePythonStockDailyEventFixture() throws Exception {
        StockDailyEvent event = objectMapper.readValue(
                fixtureJson(),
                StockDailyEvent.class
        );

        assertEquals("TUSHARE:000001.SZ:20260703", event.eventId());
        assertEquals("daily-tushare-20260703-10001", event.traceId());
        assertEquals("000001.SZ", event.tsCode());
        assertEquals(LocalDate.of(2026, 7, 3), event.tradeDate());
        assertEquals(new BigDecimal("10.25"), event.open());
        assertEquals(new BigDecimal("10.68"), event.high());
        assertEquals(new BigDecimal("10.12"), event.low());
        assertEquals(new BigDecimal("10.55"), event.close());
        assertEquals(new BigDecimal("10.20"), event.preClose());
        assertEquals(new BigDecimal("0.35"), event.change());
        assertEquals(new BigDecimal("3.4314"), event.pctChg());
        assertEquals(new BigDecimal("1250345.00"), event.volume());
        assertEquals(new BigDecimal("13054890.25"), event.amount());
        assertEquals("TUSHARE", event.source());
        assertEquals(
                OffsetDateTime.parse("2026-07-03T15:00:00+08:00"),
                event.eventTime()
        );
        assertEquals(
                OffsetDateTime.parse("2026-07-03T16:20:30+08:00"),
                event.ingestTime()
        );
        assertEquals(1, event.schemaVersion());
    }

    @Test
    void shouldRejectUnknownJsonField() throws Exception {
        String json = fixtureJson().replace(
                "\"schemaVersion\": 1",
                "\"schemaVersion\": 1, \"unexpectedField\": \"value\""
        );

        assertThrows(
                UnrecognizedPropertyException.class,
                () -> objectMapper.readValue(json, StockDailyEvent.class)
        );
    }

    @Test
    void shouldAllowNullableChangeFields() throws Exception {
        String json = fixtureJson()
                .replace("\"change\": 0.35", "\"change\": null")
                .replace("\"pctChg\": 3.4314", "\"pctChg\": null");

        StockDailyEvent event = objectMapper.readValue(
                json,
                StockDailyEvent.class
        );

        assertNull(event.change());
        assertNull(event.pctChg());
    }

    private String fixtureJson() throws Exception {
        Path fixture = Path.of(
                "..",
                "..",
                "docs",
                "examples",
                "stock_daily_event_v1.json"
        );
        return Files.readString(fixture);
    }
}
