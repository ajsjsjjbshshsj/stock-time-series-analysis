package com.stock.consumer.monitoring;

import org.springframework.stereotype.Service;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.concurrent.atomic.AtomicReference;
import java.util.concurrent.atomic.LongAdder;

/** Thread-safe in-memory counters for the current consumer process. */
@Service
public final class ConsumerStatisticsService {

    private final LongAdder totalConsumed = new LongAdder();
    private final LongAdder successCount = new LongAdder();
    private final LongAdder failureCount = new LongAdder();
    private final LongAdder dailyCount = new LongAdder();
    private final LongAdder basicCount = new LongAdder();
    private final LongAdder jsonFailureCount = new LongAdder();
    private final LongAdder validationFailureCount = new LongAdder();
    private final LongAdder unsupportedSchemaCount = new LongAdder();
    private final LongAdder deadLetterPublishFailureCount = new LongAdder();
    private final AtomicReference<OffsetDateTime> lastConsumedAt = new AtomicReference<>();

    public void recordDailySuccess() {
        recordPrimaryOutcome(true);
        dailyCount.increment();
    }

    public void recordBasicSuccess() {
        recordPrimaryOutcome(true);
        basicCount.increment();
    }

    public void recordJsonFailure() {
        recordPrimaryOutcome(false);
        jsonFailureCount.increment();
    }

    public void recordValidationFailure() {
        recordPrimaryOutcome(false);
        validationFailureCount.increment();
    }

    public void recordUnsupportedSchemaFailure() {
        recordPrimaryOutcome(false);
        unsupportedSchemaCount.increment();
    }

    public void recordDeadLetterPublishFailure() {
        deadLetterPublishFailureCount.increment();
    }

    public ConsumerStatisticsSnapshot snapshot() {
        return new ConsumerStatisticsSnapshot(
                totalConsumed.sum(), successCount.sum(), failureCount.sum(), dailyCount.sum(), basicCount.sum(),
                jsonFailureCount.sum(), validationFailureCount.sum(), unsupportedSchemaCount.sum(),
                deadLetterPublishFailureCount.sum(), lastConsumedAt.get());
    }

    private void recordPrimaryOutcome(boolean successful) {
        totalConsumed.increment();
        if (successful) {
            successCount.increment();
        } else {
            failureCount.increment();
        }
        lastConsumedAt.set(OffsetDateTime.now(ZoneOffset.UTC));
    }
}
