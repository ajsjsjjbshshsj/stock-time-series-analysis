package com.stock.common.model;

import java.time.LocalDate;

/** One canonical partitioned stream carries indicators, seed barriers and repair evidence.
 * SEED_COMPLETE is per-key only, never a generation readiness/activation declaration.
 */
public record GenerationOutputEnvelope(
        String generation, Integer schemaVersion, String kind, String tsCode,
        LocalDate tradeDate, Long sourceRevision, StockDailyIndicatorEvent indicator,
        String reason, String inputTopic, Integer inputPartition, Long inputOffset) {
}
