package com.stock.market.ingest;

import com.stock.common.model.IndicatorGenerationBinding;
import com.stock.market.storage.GenerationIndicatorRepository;
import com.stock.market.storage.GenerationRegistry;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.support.Acknowledgment;
import org.springframework.stereotype.Component;
import java.util.*;
import java.util.regex.Pattern;

@Component
@ConditionalOnProperty(name = "market.generation-listener-enabled", havingValue = "true")
public class GenerationIndicatorListener {
    public static final String CONSUMER_GROUP = "stock-clickhouse-indicators-v2";
    public static final String TOPIC_PATTERN = "stock\\.indicators\\.[0-9a-f]{32}\\.daily\\.v2";
    private static final Pattern NAMESPACE = Pattern.compile("stock\\.indicators\\.([0-9a-f]{32})\\.daily\\.v2");
    private final GenerationRegistry registry;
    private final GenerationIndicatorRepository repository;

    public GenerationIndicatorListener(GenerationRegistry registry, GenerationIndicatorRepository repository) {
        this.registry = registry;
        this.repository = repository;
    }

    @KafkaListener(id = "marketGenerationIndicators", topicPattern = TOPIC_PATTERN,
            groupId = CONSUMER_GROUP, containerFactory = "generationIndicatorFactory",
            autoStartup = "${market.generation-listener-enabled:false}")
    public void receive(List<ConsumerRecord<String,String>> records, Acknowledgment ack) {
        if (records == null || records.size() > 500) throw new IllegalArgumentException("Invalid generation batch");
        var bindings = new HashMap<String, IndicatorGenerationBinding>();
        var validated = new ArrayList<GenerationRecord>(records.size());
        for (var record : records) {
            if (record == null) throw new IllegalArgumentException("Invalid generation record");
            var namespace = NAMESPACE.matcher(record.topic());
            if (!namespace.matches()) throw new IllegalArgumentException("Invalid generation topic");
            var binding = bindings.computeIfAbsent(namespace.group(1), registry::require);
            validated.add(GenerationRecord.parse(binding, record));
        }
        repository.insertBatch(validated);
        ack.acknowledge();
    }
}
