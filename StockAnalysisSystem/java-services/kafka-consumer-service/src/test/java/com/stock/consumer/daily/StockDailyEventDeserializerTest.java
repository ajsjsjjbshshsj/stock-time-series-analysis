package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.stock.common.model.StockDailyEvent;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StockDailyEventDeserializerTest {

    private final StockDailyEventDeserializer deserializer =
            new StockDailyEventDeserializer(new ObjectMapper());

    @Test
    void deserializesProtocolExampleWithoutLosingTypesOrPrecision() throws Exception {
        StockDailyEvent event = deserializer.deserialize(validJson());

        assertEquals("000001.SZ", event.tsCode());
        assertEquals(LocalDate.of(2026, 7, 3), event.tradeDate());
        assertEquals(new BigDecimal("10.55"), event.close());
        assertEquals(new BigDecimal("13054890.25"), event.amount());
        assertEquals(OffsetDateTime.parse("2026-07-03T15:00:00+08:00"), event.eventTime());
        assertEquals(OffsetDateTime.parse("2026-07-03T16:20:30+08:00"), event.ingestTime());
    }

    @Test
    void rejectsMalformedJson() {
        assertThrows(JsonProcessingException.class, () -> deserializer.deserialize("{not-json}"));
    }

    @Test
    void rejectsUnknownJsonField() {
        String json = validJson().replace("\"schemaVersion\": 1", "\"schemaVersion\": 1, \"unexpected\": true");

        assertThrows(JsonProcessingException.class, () -> deserializer.deserialize(json));
    }

    static String validJson() {
        return """
                {
                  "eventId": "TUSHARE:000001.SZ:20260703",
                  "traceId": "daily-tushare-20260703-10001",
                  "tsCode": "000001.SZ",
                  "tradeDate": "2026-07-03",
                  "open": 10.25,
                  "high": 10.68,
                  "low": 10.12,
                  "close": 10.55,
                  "preClose": 10.20,
                  "change": 0.35,
                  "pctChg": 3.4314,
                  "volume": 1250345.00,
                  "amount": 13054890.25,
                  "source": "TUSHARE",
                  "eventTime": "2026-07-03T15:00:00+08:00",
                  "ingestTime": "2026-07-03T16:20:30+08:00",
                  "schemaVersion": 1
                }
                """;
    }
}
