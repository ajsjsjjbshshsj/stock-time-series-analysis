package com.stock.consumer.daily;

import io.micrometer.core.instrument.simple.SimpleMeterRegistry;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;

class ConsumerMetricsTest {

    @Test
    void recordsSuccessAndFailuresByReason() {
        SimpleMeterRegistry registry = new SimpleMeterRegistry();
        ConsumerMetrics metrics = new ConsumerMetrics(registry);

        metrics.recordSuccess();
        metrics.recordFailure("validation");
        metrics.recordFailure("validation");

        assertEquals(1.0, registry.get("stock.kafka.consumer.success").counter().count());
        assertEquals(2.0, registry.get("stock.kafka.consumer.failure")
                .tag("reason", "validation").counter().count());
    }
}
