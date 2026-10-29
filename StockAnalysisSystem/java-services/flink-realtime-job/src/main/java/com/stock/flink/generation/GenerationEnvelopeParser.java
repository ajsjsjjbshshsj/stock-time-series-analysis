package com.stock.flink.generation;

import com.stock.common.model.IndicatorGenerationBinding;
import com.stock.common.model.GenerationInputEnvelope;
import com.stock.common.validation.StockDailyEventRules;
import com.stock.flink.model.RawKafkaRecord;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.*;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import java.util.Objects;
import java.util.Set;

public final class GenerationEnvelopeParser {
    private final IndicatorGenerationBinding binding;
    private final ObjectMapper mapper;

    public GenerationEnvelopeParser(IndicatorGenerationBinding binding) {
        this.binding = Objects.requireNonNull(binding);
        this.mapper = jsonMapper();
    }

    public static ObjectMapper jsonMapper() {
        return new ObjectMapper().registerModule(new JavaTimeModule())
                .enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                .enable(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES)
                .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .disable(DeserializationFeature.ACCEPT_FLOAT_AS_INT)
                .disable(MapperFeature.ALLOW_COERCION_OF_SCALARS)
                .disable(DeserializationFeature.ADJUST_DATES_TO_CONTEXT_TIME_ZONE)
                .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    }

    public GenerationInputRecord parse(RawKafkaRecord raw) {
        if (raw == null || raw.payload() == null) throw invalid();
        try {
            GenerationInputEnvelope envelope = mapper.readValue(raw.payload(), GenerationInputEnvelope.class);
            validate(raw, envelope);
            return new GenerationInputRecord(raw, envelope);
        } catch (JsonProcessingException e) {
            // Do not leak payloads/credentials from malformed transport into diagnostics.
            throw invalid();
        }
    }

    public void validate(RawKafkaRecord raw, GenerationInputEnvelope e) {
        if (raw == null || e == null || !Integer.valueOf(2).equals(e.schemaVersion())
                || e.tsCode() == null || e.tsCode().isBlank() || !e.tsCode().equals(raw.key())
                || raw.offset() < 0 || e.sourceRevision() == null || e.sourceRevision() < 0
                || e.operation() == null || !Set.of("SNAPSHOT", "SEED_END", "UPSERT", "DELETE").contains(e.operation())) {
            throw invalid();
        }
        binding.requireInput(raw.topic(), raw.partition(), e.generation());
        if (raw.partition() != binding.partitionFor(e.tsCode())) throw invalid();
        if (e.operation().equals("SEED_END")) {
            if (e.sourceRevision() != 0 || e.tradeDate() != null || e.event() != null) throw invalid();
            return;
        }
        if (e.tradeDate() == null) throw invalid();
        if (!e.operation().equals("SNAPSHOT") && e.sourceRevision() == 0) throw invalid();
        if (e.operation().equals("DELETE")) {
            if (e.event() != null) throw invalid();
            return;
        }
        if (!StockDailyEventRules.violations(raw.key(), e.event(), 1).isEmpty()
                || !e.tradeDate().equals(e.event().tradeDate())) throw invalid();
    }

    private static IllegalArgumentException invalid() {
        return new IllegalArgumentException("Invalid generation input envelope");
    }
}
