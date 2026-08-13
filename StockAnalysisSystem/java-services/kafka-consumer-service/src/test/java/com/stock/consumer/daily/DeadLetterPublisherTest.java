package com.stock.consumer.daily;

import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.support.SendResult;

import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DeadLetterPublisherTest {

    @Test
    void publishesOriginalRecordWithDiagnosticHeaders() {
        @SuppressWarnings("unchecked")
        KafkaTemplate<String, String> template = mock(KafkaTemplate.class);
        CompletableFuture<SendResult<String, String>> future = CompletableFuture.completedFuture(null);
        when(template.send(org.mockito.ArgumentMatchers.<ProducerRecord<String, String>>any())).thenReturn(future);
        DeadLetterPublisher publisher = new DeadLetterPublisher(template, "stock.dead-letter.v1");
        ConsumerRecord<String, String> source = new ConsumerRecord<>("stock.ods.daily.v1", 2, 42L,
                "000001.SZ", "{bad-json}");

        assertEquals(future, publisher.publish(source, "deserialization", "invalid JSON"));

        @SuppressWarnings({"unchecked", "rawtypes"})
        ArgumentCaptor<ProducerRecord<String, String>> captor =
                (ArgumentCaptor) ArgumentCaptor.forClass(ProducerRecord.class);
        verify(template).send(captor.capture());
        ProducerRecord<String, String> record = captor.getValue();
        assertEquals("stock.dead-letter.v1", record.topic());
        assertEquals("000001.SZ", record.key());
        assertEquals("{bad-json}", record.value());
        assertHeader(record, "x-original-topic", "stock.ods.daily.v1");
        assertHeader(record, "x-original-partition", "2");
        assertHeader(record, "x-original-offset", "42");
        assertHeader(record, "x-error-type", "deserialization");
        assertHeader(record, "x-error-message", "invalid JSON");
    }

    private static void assertHeader(ProducerRecord<String, String> record, String name, String expected) {
        assertEquals(expected, new String(record.headers().lastHeader(name).value(), StandardCharsets.UTF_8));
    }
}
