package com.stock.consumer.daily;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.header.internals.RecordHeader;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;
import org.springframework.stereotype.Component;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;

/** Publishes the untouched source record with enough metadata for diagnosis and replay. */
@Component
public final class DeadLetterPublisher {

    private final KafkaTemplate<String, String> kafkaTemplate;
    private final String deadLetterTopic;

    public DeadLetterPublisher(KafkaTemplate<String, String> kafkaTemplate,
                               @Value("${stock.kafka.dead-letter-topic:stock.dead-letter.v1}") String deadLetterTopic) {
        this.kafkaTemplate = kafkaTemplate;
        this.deadLetterTopic = deadLetterTopic;
    }

    public CompletableFuture<SendResult<String, String>> publish(ConsumerRecord<String, String> source,
                                                                  String errorType,
                                                                  String errorMessage) {
        ProducerRecord<String, String> target = new ProducerRecord<>(deadLetterTopic, source.key(), source.value());
        addHeader(target, "x-original-topic", source.topic());
        addHeader(target, "x-original-partition", Integer.toString(source.partition()));
        addHeader(target, "x-original-offset", Long.toString(source.offset()));
        addHeader(target, "x-error-type", errorType);
        addHeader(target, "x-error-message", errorMessage);
        return kafkaTemplate.send(target);
    }

    private static void addHeader(ProducerRecord<String, String> record, String name, String value) {
        record.headers().add(new RecordHeader(name, safe(value).getBytes(StandardCharsets.UTF_8)));
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
