package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.stock.common.model.StockDailyEvent;
import com.stock.consumer.monitoring.ConsumerError;
import com.stock.consumer.monitoring.ConsumerErrorStore;
import com.stock.consumer.monitoring.ConsumerStatisticsService;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

import java.time.OffsetDateTime;
import java.time.ZoneOffset;
import java.util.Locale;
import java.util.regex.Pattern;

/** Coordinates consumption while keeping protocol and transport concerns in focused collaborators. */
@Component
public final class StockDailyEventConsumer {

    private static final Logger log = LoggerFactory.getLogger(StockDailyEventConsumer.class);
    private static final Pattern SAFE_STOCK_KEY = Pattern.compile("[0-9]{6}\\.(SZ|SH|BJ)");

    private final StockDailyEventDeserializer deserializer;
    private final StockDailyEventValidator validator;
    private final DeadLetterPublisher deadLetterPublisher;
    private final ConsumerMetrics metrics;
    private final ConsumerStatisticsService statistics;
    private final ConsumerErrorStore errorStore;

    public StockDailyEventConsumer(StockDailyEventDeserializer deserializer,
                                   StockDailyEventValidator validator,
                                   DeadLetterPublisher deadLetterPublisher,
                                   ConsumerMetrics metrics,
                                   ConsumerStatisticsService statistics,
                                   ConsumerErrorStore errorStore) {
        this.deserializer = deserializer;
        this.validator = validator;
        this.deadLetterPublisher = deadLetterPublisher;
        this.metrics = metrics;
        this.statistics = statistics;
        this.errorStore = errorStore;
    }

    @KafkaListener(topics = "${stock.kafka.daily-topic}", groupId = "${spring.kafka.consumer.group-id}")
    public void consume(ConsumerRecord<String, String> record, Acknowledgment acknowledgment) {
        try {
            StockDailyEvent event = deserializer.deserialize(record.value());
            validator.validate(record.key(), event);
            metrics.recordSuccess();
            statistics.recordDailySuccess();
            log.info("Consumed stock daily event eventId={} topic={} partition={} offset={} key={}",
                    event.eventId(), record.topic(), record.partition(), record.offset(), record.key());
            acknowledgment.acknowledge();
        } catch (JsonProcessingException exception) {
            isolate(record, acknowledgment, "deserialization", "Malformed JSON payload");
        } catch (UnsupportedSchemaVersionException exception) {
            isolate(record, acknowledgment, "unsupported_schema", exception.getMessage());
        } catch (StockDailyEventValidationException exception) {
            isolate(record, acknowledgment, "validation", exception.getMessage());
        }
    }

    private void isolate(ConsumerRecord<String, String> record,
                         Acknowledgment acknowledgment,
                         String reason,
                         String message) {
        metrics.recordFailure(reason);
        recordPrimaryFailure(reason);
        errorStore.add(error(record, reason, message));
        log.warn("Rejecting stock daily event reason={} topic={} partition={} offset={} key={} message={}",
                reason, record.topic(), record.partition(), record.offset(), safeMonitoringKey(record.key()), message);
        try {
            deadLetterPublisher.publish(record, reason, message).join();
            acknowledgment.acknowledge();
        } catch (RuntimeException exception) {
            metrics.recordFailure("dead_letter_publish");
            statistics.recordDeadLetterPublishFailure();
            errorStore.add(error(record, "dead_letter_publish", exception.getMessage()));
            log.error("Dead-letter publication failed topic={} partition={} offset={} key={}",
                    record.topic(), record.partition(), record.offset(), safeMonitoringKey(record.key()), exception);
            throw exception;
        }
    }

    private void recordPrimaryFailure(String reason) {
        switch (reason) {
            case "deserialization" -> statistics.recordJsonFailure();
            case "unsupported_schema" -> statistics.recordUnsupportedSchemaFailure();
            case "validation" -> statistics.recordValidationFailure();
            default -> throw new IllegalArgumentException("Unknown consumer failure reason: " + reason);
        }
    }

    private static ConsumerError error(ConsumerRecord<String, String> record,
                                             String errorType,
                                             String message) {
        return new ConsumerError(OffsetDateTime.now(ZoneOffset.UTC), errorType.toUpperCase(Locale.ROOT), message,
                record.topic(), safeMonitoringKey(record.key()), record.partition(), record.offset());
    }

    private static String safeMonitoringKey(String key) {
        return key != null && SAFE_STOCK_KEY.matcher(key).matches() ? key : "<redacted>";
    }
}
