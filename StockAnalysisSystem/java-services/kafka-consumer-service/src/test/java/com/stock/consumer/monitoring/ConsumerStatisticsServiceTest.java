package com.stock.consumer.monitoring;

import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

class ConsumerStatisticsServiceTest {

    @Test
    void recordsSuccessfulDailyEvent() {
        ConsumerStatisticsService statistics = new ConsumerStatisticsService();

        statistics.recordDailySuccess();

        ConsumerStatisticsSnapshot snapshot = statistics.snapshot();
        assertEquals(1, snapshot.totalConsumed());
        assertEquals(1, snapshot.successCount());
        assertEquals(0, snapshot.failureCount());
        assertEquals(1, snapshot.dailyEventCount());
        assertNotNull(snapshot.lastConsumedTime());
    }

    @Test
    void classifiesPrimaryFailuresWithoutDoubleCounting() {
        ConsumerStatisticsService statistics = new ConsumerStatisticsService();

        statistics.recordJsonFailure();
        statistics.recordValidationFailure();
        statistics.recordUnsupportedSchemaFailure();

        ConsumerStatisticsSnapshot snapshot = statistics.snapshot();
        assertEquals(3, snapshot.totalConsumed());
        assertEquals(3, snapshot.failureCount());
        assertEquals(1, snapshot.jsonParseFailureCount());
        assertEquals(1, snapshot.validationFailureCount());
        assertEquals(1, snapshot.unsupportedSchemaCount());
    }

    @Test
    void deadLetterFailureIsSecondaryAndDoesNotCountRecordTwice() {
        ConsumerStatisticsService statistics = new ConsumerStatisticsService();

        statistics.recordJsonFailure();
        statistics.recordDeadLetterPublishFailure();

        ConsumerStatisticsSnapshot snapshot = statistics.snapshot();
        assertEquals(1, snapshot.totalConsumed());
        assertEquals(1, snapshot.failureCount());
        assertEquals(1, snapshot.jsonParseFailureCount());
        assertEquals(1, snapshot.deadLetterPublishFailureCount());
    }

    @Test
    void remainsAccurateUnderConcurrentUpdates() throws Exception {
        ConsumerStatisticsService statistics = new ConsumerStatisticsService();
        CountDownLatch start = new CountDownLatch(1);
        List<Thread> workers = new ArrayList<>();
        for (int worker = 0; worker < 8; worker++) {
            Thread thread = new Thread(() -> {
                try {
                    start.await();
                    for (int increment = 0; increment < 1_000; increment++) {
                        statistics.recordDailySuccess();
                    }
                } catch (InterruptedException exception) {
                    Thread.currentThread().interrupt();
                    throw new IllegalStateException(exception);
                }
            });
            workers.add(thread);
            thread.start();
        }

        start.countDown();
        for (Thread worker : workers) {
            worker.join();
        }

        ConsumerStatisticsSnapshot snapshot = statistics.snapshot();
        assertEquals(8_000, snapshot.totalConsumed());
        assertEquals(8_000, snapshot.successCount());
        assertEquals(8_000, snapshot.dailyEventCount());
    }
}
