package com.stock.common.model;

import java.time.LocalDate;
import java.time.OffsetDateTime;

/** Cross-language Kafka contract for one stock basic-information snapshot. */
public record StockBasicEvent(
        String eventId,
        String traceId,
        String tsCode,
        String symbol,
        String name,
        String area,
        String industry,
        LocalDate listDate,
        String source,
        OffsetDateTime eventTime,
        OffsetDateTime ingestTime,
        Integer schemaVersion
) {
}
