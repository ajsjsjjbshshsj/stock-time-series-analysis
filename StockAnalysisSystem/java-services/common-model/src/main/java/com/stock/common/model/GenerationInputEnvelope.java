package com.stock.common.model;

import java.time.LocalDate;

/** V2 generation wire envelope; nested daily events retain their V1 schema. */
public record GenerationInputEnvelope(
        String generation, Integer schemaVersion, String operation, Long sourceRevision,
        String tsCode, LocalDate tradeDate, StockDailyEvent event) {
}
