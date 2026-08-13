package com.stock.consumer.daily;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.MeterRegistry;
import org.springframework.stereotype.Component;

/** Centralizes the metric names used to observe the daily-event consumer. */
@Component
public final class ConsumerMetrics {

    private final MeterRegistry meterRegistry;
    private final Counter success;

    public ConsumerMetrics(MeterRegistry meterRegistry) {
        this.meterRegistry = meterRegistry;
        this.success = meterRegistry.counter("stock.kafka.consumer.success");
    }

    public void recordSuccess() {
        success.increment();
    }

    public void recordFailure(String reason) {
        meterRegistry.counter("stock.kafka.consumer.failure", "reason", reason).increment();
    }
}
