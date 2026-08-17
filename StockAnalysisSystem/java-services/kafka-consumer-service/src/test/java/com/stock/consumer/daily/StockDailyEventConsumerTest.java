package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonParseException;
import com.stock.common.model.StockDailyEvent;
import com.stock.consumer.monitoring.ConsumerError;
import com.stock.consumer.monitoring.ConsumerErrorStore;
import com.stock.consumer.monitoring.ConsumerStatisticsService;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.kafka.support.Acknowledgment;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class StockDailyEventConsumerTest {

    private StockDailyEventDeserializer deserializer;
    private StockDailyEventValidator validator;
    private DeadLetterPublisher deadLetterPublisher;
    private ConsumerMetrics metrics;
    private ConsumerStatisticsService statistics;
    private ConsumerErrorStore errorStore;
    private Acknowledgment acknowledgment;
    private StockDailyEventConsumer consumer;

    @BeforeEach
    void setUp() {
        deserializer = mock(StockDailyEventDeserializer.class);
        validator = mock(StockDailyEventValidator.class);
        deadLetterPublisher = mock(DeadLetterPublisher.class);
        metrics = mock(ConsumerMetrics.class);
        statistics = mock(ConsumerStatisticsService.class);
        errorStore = mock(ConsumerErrorStore.class);
        acknowledgment = mock(Acknowledgment.class);
        consumer = new StockDailyEventConsumer(
                deserializer, validator, deadLetterPublisher, metrics, statistics, errorStore);
    }

    @Test
    void acknowledgesValidRecordAfterValidation() throws Exception {
        ConsumerRecord<String, String> record = record("{}");
        StockDailyEvent event = validEvent();
        when(deserializer.deserialize("{}")).thenReturn(event);

        consumer.consume(record, acknowledgment);

        verify(validator).validate("000001.SZ", event);
        verify(metrics).recordSuccess();
        verify(statistics).recordDailySuccess();
        verify(acknowledgment).acknowledge();
        verify(deadLetterPublisher, never()).publish(record, "deserialization", "");
    }

    @Test
    void sendsMalformedJsonToDeadLetterBeforeAcknowledging() throws Exception {
        ConsumerRecord<String, String> record = record("{bad}");
        when(deserializer.deserialize("{bad}"))
                .thenThrow(new JsonParseException(null, "invalid JSON"));
        when(deadLetterPublisher.publish(record, "deserialization", "Malformed JSON payload"))
                .thenReturn(CompletableFuture.completedFuture(null));

        consumer.consume(record, acknowledgment);

        verify(metrics).recordFailure("deserialization");
        verify(statistics).recordJsonFailure();
        ArgumentCaptor<ConsumerError> error = ArgumentCaptor.forClass(ConsumerError.class);
        verify(errorStore).add(error.capture());
        assertEquals("DESERIALIZATION", error.getValue().errorType());
        assertEquals("Malformed JSON payload", error.getValue().message());
        assertEquals("stock.ods.daily.v1", error.getValue().topic());
        assertEquals("000001.SZ", error.getValue().key());
        assertEquals(0, error.getValue().partition());
        assertEquals(7L, error.getValue().offset());
        verify(deadLetterPublisher).publish(record, "deserialization", "Malformed JSON payload");
        verify(acknowledgment).acknowledge();
    }

    @Test
    void redactsUnsafeKafkaKeyFromMonitoringButPreservesItForDeadLetter() throws Exception {
        ConsumerRecord<String, String> record = new ConsumerRecord<>(
                "stock.ods.daily.v1", 0, 8L, "token=secret\r\nforged", "{bad}");
        when(deserializer.deserialize("{bad}"))
                .thenThrow(new JsonParseException(null, "secret payload fragment"));
        when(deadLetterPublisher.publish(record, "deserialization", "Malformed JSON payload"))
                .thenReturn(CompletableFuture.completedFuture(null));

        consumer.consume(record, acknowledgment);

        ArgumentCaptor<ConsumerError> error = ArgumentCaptor.forClass(ConsumerError.class);
        verify(errorStore).add(error.capture());
        assertEquals("<redacted>", error.getValue().key());
        verify(deadLetterPublisher).publish(record, "deserialization", "Malformed JSON payload");
    }

    @Test
    void sendsProtocolViolationToDeadLetterBeforeAcknowledging() throws Exception {
        ConsumerRecord<String, String> record = record("{}");
        StockDailyEvent event = validEvent();
        when(deserializer.deserialize("{}")).thenReturn(event);
        StockDailyEventValidationException failure =
                new StockDailyEventValidationException(List.of("kafkaKey must equal tsCode"));
        org.mockito.Mockito.doThrow(failure).when(validator).validate("000001.SZ", event);
        when(deadLetterPublisher.publish(record, "validation", failure.getMessage()))
                .thenReturn(CompletableFuture.completedFuture(null));

        consumer.consume(record, acknowledgment);

        verify(metrics).recordFailure("validation");
        verify(statistics).recordValidationFailure();
        verify(deadLetterPublisher).publish(record, "validation", failure.getMessage());
        verify(acknowledgment).acknowledge();
    }

    @Test
    void classifiesUnsupportedSchemaSeparately() throws Exception {
        ConsumerRecord<String, String> record = record("{}");
        StockDailyEvent event = validEvent();
        when(deserializer.deserialize("{}")).thenReturn(event);
        UnsupportedSchemaVersionException failure = new UnsupportedSchemaVersionException(2, 1);
        org.mockito.Mockito.doThrow(failure).when(validator).validate("000001.SZ", event);
        when(deadLetterPublisher.publish(record, "unsupported_schema", failure.getMessage()))
                .thenReturn(CompletableFuture.completedFuture(null));

        consumer.consume(record, acknowledgment);

        verify(statistics).recordUnsupportedSchemaFailure();
        verify(deadLetterPublisher).publish(record, "unsupported_schema", failure.getMessage());
        verify(acknowledgment).acknowledge();
    }

    @Test
    void doesNotAcknowledgeWhenDeadLetterPublicationFails() throws Exception {
        ConsumerRecord<String, String> record = record("{bad}");
        when(deserializer.deserialize("{bad}"))
                .thenThrow(new JsonParseException(null, "invalid JSON"));
        CompletableFuture<org.springframework.kafka.support.SendResult<String, String>> failed =
                CompletableFuture.failedFuture(new IllegalStateException("Kafka unavailable"));
        when(deadLetterPublisher.publish(record, "deserialization", "Malformed JSON payload")).thenReturn(failed);

        assertThrows(CompletionException.class, () -> consumer.consume(record, acknowledgment));

        verify(metrics).recordFailure("deserialization");
        verify(metrics).recordFailure("dead_letter_publish");
        verify(statistics).recordDeadLetterPublishFailure();
        verify(acknowledgment, never()).acknowledge();
    }

    @Test
    void doesNotAcknowledgeWhenDeadLetterPublisherThrowsSynchronously() throws Exception {
        ConsumerRecord<String, String> record = record("{bad}");
        when(deserializer.deserialize("{bad}"))
                .thenThrow(new JsonParseException(null, "invalid JSON"));
        when(deadLetterPublisher.publish(record, "deserialization", "Malformed JSON payload"))
                .thenThrow(new IllegalStateException("producer is closed"));

        assertThrows(IllegalStateException.class, () -> consumer.consume(record, acknowledgment));

        verify(metrics).recordFailure("dead_letter_publish");
        verify(statistics).recordDeadLetterPublishFailure();
        verify(acknowledgment, never()).acknowledge();
    }

    private static ConsumerRecord<String, String> record(String value) {
        return new ConsumerRecord<>("stock.ods.daily.v1", 0, 7L, "000001.SZ", value);
    }

    private static StockDailyEvent validEvent() {
        return new StockDailyEvent("TUSHARE:000001.SZ:20260703", "daily-tushare-20260703-10001",
                "000001.SZ", LocalDate.of(2026, 7, 3), new BigDecimal("10.25"), new BigDecimal("10.68"),
                new BigDecimal("10.12"), new BigDecimal("10.55"), new BigDecimal("10.20"),
                new BigDecimal("0.35"), new BigDecimal("3.4314"), new BigDecimal("1250345.00"),
                new BigDecimal("13054890.25"), "TUSHARE", OffsetDateTime.parse("2026-07-03T15:00:00+08:00"),
                OffsetDateTime.parse("2026-07-03T16:20:30+08:00"), 1);
    }
}
