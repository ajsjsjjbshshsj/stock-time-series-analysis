package com.stock.market.config;

import com.stock.market.ingest.GenerationIndicatorListener;
import org.apache.kafka.clients.consumer.ConsumerConfig;
import org.apache.kafka.common.serialization.StringDeserializer;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.*;
import org.springframework.kafka.config.ConcurrentKafkaListenerContainerFactory;
import org.springframework.kafka.core.DefaultKafkaConsumerFactory;
import org.springframework.kafka.listener.*;
import java.util.HashMap;

/** Additive V2 factory; disabled by default and independent of the V1 consumer. */
@Configuration
@ConditionalOnProperty(name = "market.generation-listener-enabled", havingValue = "true")
public class GenerationKafkaConfiguration {
    @Bean
    ConcurrentKafkaListenerContainerFactory<String, String> generationIndicatorFactory(
            @Value("${spring.kafka.bootstrap-servers}") String servers,
            @Value("${market.kafka-group:stock-clickhouse-indicators-v1}") String v1Group) {
        if (GenerationIndicatorListener.CONSUMER_GROUP.equals(v1Group))
            throw new IllegalArgumentException("Generation and V1 consumer groups must be distinct");
        var props = new HashMap<String, Object>();
        props.put(ConsumerConfig.BOOTSTRAP_SERVERS_CONFIG, servers);
        props.put(ConsumerConfig.GROUP_ID_CONFIG, GenerationIndicatorListener.CONSUMER_GROUP);
        props.put(ConsumerConfig.ENABLE_AUTO_COMMIT_CONFIG, false);
        props.put(ConsumerConfig.ALLOW_AUTO_CREATE_TOPICS_CONFIG, false);
        props.put(ConsumerConfig.ISOLATION_LEVEL_CONFIG, "read_committed");
        props.put(ConsumerConfig.AUTO_OFFSET_RESET_CONFIG, "earliest");
        props.put(ConsumerConfig.MAX_POLL_RECORDS_CONFIG, 500);
        var factory = new ConcurrentKafkaListenerContainerFactory<String, String>();
        factory.setConsumerFactory(new DefaultKafkaConsumerFactory<>(props, new StringDeserializer(), new StringDeserializer()));
        factory.setBatchListener(true);
        factory.getContainerProperties().setAckMode(ContainerProperties.AckMode.MANUAL_IMMEDIATE);
        factory.getContainerProperties().setSyncCommits(true);
        // A failed/partial batch stops without recovery/commit; replay is idempotent.
        factory.setCommonErrorHandler(new CommonContainerStoppingErrorHandler());
        return factory;
    }
}
