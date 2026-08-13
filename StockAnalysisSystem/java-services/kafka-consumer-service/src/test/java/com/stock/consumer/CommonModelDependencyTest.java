package com.stock.consumer;

import com.stock.common.model.StockDailyEvent;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;

class CommonModelDependencyTest {

    @Test
    void consumerModuleCanUseSharedStockDailyEvent() {
        StockDailyEvent event = new StockDailyEvent(
                "TUSHARE:000001.SZ:20260703",
                "daily-tushare-20260703-10001",
                "000001.SZ",
                LocalDate.of(2026, 7, 3),
                new BigDecimal("10.25"),
                new BigDecimal("10.68"),
                new BigDecimal("10.12"),
                new BigDecimal("10.55"),
                new BigDecimal("10.20"),
                new BigDecimal("0.35"),
                new BigDecimal("3.4314"),
                new BigDecimal("1250345.00"),
                new BigDecimal("13054890.25"),
                "TUSHARE",
                OffsetDateTime.parse("2026-07-03T15:00:00+08:00"),
                OffsetDateTime.parse("2026-07-03T16:20:30+08:00"),
                1
        );

        assertEquals("000001.SZ", event.tsCode());
        assertEquals(new BigDecimal("10.55"), event.close());
    }
}
