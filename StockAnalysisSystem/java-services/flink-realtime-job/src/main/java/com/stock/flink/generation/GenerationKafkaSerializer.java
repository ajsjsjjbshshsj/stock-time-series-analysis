package com.stock.flink.generation;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.stock.common.model.GenerationOutputEnvelope;
import com.stock.common.model.IndicatorGenerationBinding;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.kafka.clients.producer.ProducerRecord;
import java.nio.charset.StandardCharsets;
import java.util.Objects;

/** Explicit partition assignment keeps offsets comparable for a stock across all output kinds. */
public final class GenerationKafkaSerializer implements KafkaRecordSerializationSchema<GenerationOutputEnvelope> {
    private static final ObjectMapper JSON = GenerationEnvelopeParser.jsonMapper();
    private final IndicatorGenerationBinding binding;

    public GenerationKafkaSerializer(IndicatorGenerationBinding binding) {
        this.binding = Objects.requireNonNull(binding);
    }

    @Override public ProducerRecord<byte[], byte[]> serialize(
            GenerationOutputEnvelope e, KafkaSinkContext context, Long timestamp) {
        int partition = binding.partitionFor(e.tsCode());
        binding.requireOutput(binding.outputTopic(), partition, e.generation());
        int[] actual = context.getPartitionsForTopic(binding.outputTopic());
        binding.requirePartitionCount(binding.outputTopic(), actual.length);
        boolean found = false;
        for (int value : actual) if (value == partition) found = true;
        if (!found || !Integer.valueOf(2).equals(e.schemaVersion())) {
            throw new IllegalArgumentException("Invalid generation output transport");
        }
        try {
            return new ProducerRecord<>(binding.outputTopic(), partition,
                    e.tsCode().getBytes(StandardCharsets.UTF_8), JSON.writeValueAsBytes(e));
        } catch (JsonProcessingException error) {
            throw new IllegalStateException("Could not serialize generation output");
        }
    }
}
