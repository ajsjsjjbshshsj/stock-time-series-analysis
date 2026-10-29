package com.stock.market.ingest;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.*;
import com.stock.common.model.GenerationOutputEnvelope;
import com.stock.common.model.IndicatorGenerationBinding;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import java.util.Objects;
import java.util.Set;

/** Canonical output offset is the version; the input offset is provenance only. */
public record GenerationRecord(IndicatorGenerationBinding binding, GenerationOutputEnvelope envelope,
                               String topic, int partition, long offset) {
    private static final ObjectMapper JSON = IndicatorListener.mapper()
            .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
            .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
            .disable(MapperFeature.ALLOW_COERCION_OF_SCALARS);

    public GenerationRecord {
        if (binding == null || envelope == null || offset < 0 || envelope.kind() == null
                || !Integer.valueOf(2).equals(envelope.schemaVersion())
                || envelope.tsCode() == null || envelope.tsCode().isBlank()
                || envelope.inputOffset() == null || envelope.inputOffset() < 0
                || envelope.inputPartition() == null || envelope.sourceRevision() == null
                || envelope.sourceRevision() < 0) throw invalid();
        binding.requireOutput(topic, partition, envelope.generation());
        binding.requireInput(envelope.inputTopic(), envelope.inputPartition(), envelope.generation());
        int canonical = binding.partitionFor(envelope.tsCode());
        if (partition != canonical || envelope.inputPartition() != canonical) throw invalid();
        switch (envelope.kind()) {
            case "INDICATOR" -> {
                if (envelope.tradeDate() == null || envelope.indicator() == null
                        || !envelope.tradeDate().equals(envelope.indicator().tradeDate())
                        || envelope.reason() != null) throw invalid();
                new IndicatorValidator(binding.outputTopic()).validate(envelope.indicator(), envelope.tsCode(), topic);
            }
            case "SEED_COMPLETE" -> {
                if (envelope.tradeDate() != null || envelope.indicator() != null
                        || envelope.sourceRevision() != 0 || envelope.reason() != null) throw invalid();
            }
            case "REPAIR_REQUIRED" -> {
                if (envelope.indicator() != null || envelope.reason() == null
                        || !Set.of("HISTORICAL_MUTATION", "DELETE_REQUIRES_REPAIR", "KEY_REPAIR_BLOCKED").contains(envelope.reason())) throw invalid();
                if (envelope.sourceRevision() == 0) {
                    if (!envelope.reason().equals("KEY_REPAIR_BLOCKED") || envelope.tradeDate() != null) throw invalid();
                } else if (envelope.tradeDate() == null) throw invalid();
            }
            default -> throw invalid();
        }
    }

    public static GenerationRecord parse(IndicatorGenerationBinding binding, ConsumerRecord<String,String> record) {
        try {
            if (record == null || record.value() == null) throw invalid();
            var envelope = JSON.readValue(record.value(), GenerationOutputEnvelope.class);
            if (envelope == null || !Objects.equals(record.key(), envelope.tsCode())) throw invalid();
            return new GenerationRecord(binding, envelope, record.topic(), record.partition(), record.offset());
        } catch (Exception ex) {
            throw invalid(); // Do not leak transport contents through parser diagnostics.
        }
    }

    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Invalid generation output record"); }
}
