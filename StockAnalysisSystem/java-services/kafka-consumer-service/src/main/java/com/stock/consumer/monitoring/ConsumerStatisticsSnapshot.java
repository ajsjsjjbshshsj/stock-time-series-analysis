package com.stock.consumer.monitoring;

import java.time.OffsetDateTime;

/** Immutable view of the consumer counters exposed to the monitoring API. */
public record ConsumerStatisticsSnapshot(
        long totalConsumed,
        long successCount,
        long failureCount,
        long dailyEventCount,
        long basicEventCount,
        long jsonParseFailureCount,
        long validationFailureCount,
        long unsupportedSchemaCount,
        long deadLetterPublishFailureCount,
        OffsetDateTime lastConsumedTime) {
}
