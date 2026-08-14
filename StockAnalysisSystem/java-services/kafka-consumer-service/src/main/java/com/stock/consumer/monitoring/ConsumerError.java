package com.stock.consumer.monitoring;

import java.time.OffsetDateTime;

/** Safe diagnostic metadata for one consumer failure; the raw payload is intentionally excluded. */
public record ConsumerError(
        OffsetDateTime timestamp,
        String errorType,
        String message,
        String topic,
        String key,
        int partition,
        long offset) {
}
