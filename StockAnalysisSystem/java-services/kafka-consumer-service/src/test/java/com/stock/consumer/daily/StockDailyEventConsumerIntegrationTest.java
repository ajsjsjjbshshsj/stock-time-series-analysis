package com.stock.consumer.daily;

import org.apache.kafka.clients.consumer.Consumer;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.ProducerConfig;
import org.apache.kafka.common.header.Header;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.apache.kafka.common.serialization.StringSerializer;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.kafka.core.DefaultKafkaProducerFactory;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.test.EmbeddedKafkaBroker;
import org.springframework.kafka.test.context.EmbeddedKafka;
import org.springframework.kafka.test.utils.KafkaTestUtils;
import org.springframework.test.context.TestPropertySource;

import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;

@SpringBootTest
@EmbeddedKafka(partitions = 1, topics = {"stock.ods.daily.integration", "stock.dead-letter.integration"})
@TestPropertySource(properties = {
        "spring.kafka.bootstrap-servers=${spring.embedded.kafka.brokers}",
        "spring.kafka.consumer.group-id=stock-daily-integration-test",
        "stock.kafka.daily-topic=stock.ods.daily.integration",
        "stock.kafka.dead-letter-topic=stock.dead-letter.integration"
})
class StockDailyEventConsumerIntegrationTest {

    @Autowired
    private EmbeddedKafkaBroker broker;

    @Test
    void routesMalformedRecordToDeadLetterTopicWithDiagnostics() {
        KafkaTemplate<String, String> producer = producer();
        producer.send("stock.ods.daily.integration", "000001.SZ", "{bad-json}").join();

        Map<String, Object> consumerProperties = new HashMap<>(KafkaTestUtils.consumerProps(
                broker, "dead-letter-verifier", false));
        consumerProperties.put(org.apache.kafka.clients.consumer.ConsumerConfig.KEY_DESERIALIZER_CLASS_CONFIG,
                StringDeserializer.class);
        consumerProperties.put(org.apache.kafka.clients.consumer.ConsumerConfig.VALUE_DESERIALIZER_CLASS_CONFIG,
                StringDeserializer.class);
        try (Consumer<String, String> consumer = new KafkaConsumer<>(consumerProperties)) {
            broker.consumeFromAnEmbeddedTopic(consumer, "stock.dead-letter.integration");
            ConsumerRecord<String, String> record = KafkaTestUtils.getSingleRecord(
                    consumer, "stock.dead-letter.integration", Duration.ofSeconds(15));

            assertEquals("000001.SZ", record.key());
            assertEquals("{bad-json}", record.value());
            assertHeader(record, "x-original-topic", "stock.ods.daily.integration");
            assertHeader(record, "x-error-type", "deserialization");
            assertNotNull(record.headers().lastHeader("x-original-offset"));
            assertNotNull(record.headers().lastHeader("x-error-message"));
        } finally {
            producer.destroy();
        }
    }

    private KafkaTemplate<String, String> producer() {
        Map<String, Object> properties = new HashMap<>(KafkaTestUtils.producerProps(broker));
        properties.put(ProducerConfig.KEY_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        properties.put(ProducerConfig.VALUE_SERIALIZER_CLASS_CONFIG, StringSerializer.class);
        return new KafkaTemplate<>(new DefaultKafkaProducerFactory<>(properties));
    }

    private static void assertHeader(ConsumerRecord<String, String> record, String name, String expected) {
        Header header = record.headers().lastHeader(name);
        assertNotNull(header);
        assertEquals(expected, new String(header.value(), StandardCharsets.UTF_8));
    }
}
