package com.stock.consumer.daily;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.stock.common.model.StockDailyEvent;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;

/** Coordinates consumption while keeping protocol and transport concerns in focused collaborators. */
@Component
public final class StockDailyEventConsumer {

    private static final Logger log = LoggerFactory.getLogger(StockDailyEventConsumer.class);

    private final StockDailyEventDeserializer deserializer;
    private final StockDailyEventValidator validator;
    private final DeadLetterPublisher deadLetterPublisher;
    private final ConsumerMetrics metrics;

    public StockDailyEventConsumer(StockDailyEventDeserializer deserializer,
                                   StockDailyEventValidator validator,
                                   DeadLetterPublisher deadLetterPublisher,
                                   ConsumerMetrics metrics) {
        this.deserializer = deserializer;
        this.validator = validator;
        this.deadLetterPublisher = deadLetterPublisher;
        this.metrics = metrics;
    }

    @KafkaListener(topics = "${stock.kafka.daily-topic}", groupId = "${spring.kafka.consumer.group-id}")
    public void consume(ConsumerRecord<String, String> record, Acknowledgment acknowledgment) {
        try {
            StockDailyEvent event = deserializer.deserialize(record.value());
            validator.validate(record.key(), event);
            metrics.recordSuccess();
            log.info("Consumed stock daily event eventId={} topic={} partition={} offset={} key={}",
                    event.eventId(), record.topic(), record.partition(), record.offset(), record.key());
            acknowledgment.acknowledge();
        } catch (JsonProcessingException exception) {
            isolate(record, acknowledgment, "deserialization", exception.getOriginalMessage());
        } catch (StockDailyEventValidationException exception) {
            isolate(record, acknowledgment, "validation", exception.getMessage());
        }
    }

    private void isolate(ConsumerRecord<String, String> record,
                         Acknowledgment acknowledgment,
                         String reason,
                         String message) {
        metrics.recordFailure(reason);
        log.warn("Rejecting stock daily event reason={} topic={} partition={} offset={} key={} message={}",
                reason, record.topic(), record.partition(), record.offset(), record.key(), message);
        try {
            deadLetterPublisher.publish(record, reason, message).join();
            acknowledgment.acknowledge();
        } catch (RuntimeException exception) {
            metrics.recordFailure("dead_letter_publish");
            log.error("Dead-letter publication failed topic={} partition={} offset={} key={}",
                    record.topic(), record.partition(), record.offset(), record.key(), exception);
            throw exception;
        }
    }
}
