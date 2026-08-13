package com.stock.common.model;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;

public record StockDailyEvent(
        String eventId,
        String traceId,
        String tsCode,
        LocalDate tradeDate,
        BigDecimal open,
        BigDecimal high,
        BigDecimal low,
        BigDecimal close,
        BigDecimal preClose,
        BigDecimal change,
        BigDecimal pctChg,
        BigDecimal volume,
        BigDecimal amount,
        String source,
        OffsetDateTime eventTime,
        OffsetDateTime ingestTime,
        Integer schemaVersion
) {
}