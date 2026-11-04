package com.stock.flink.generation;

import com.stock.common.model.GenerationOutputEnvelope;
import com.stock.common.model.GenerationInputEnvelope;
import com.stock.common.model.IndicatorGenerationBinding;
import com.stock.flink.calculation.IndicatorCalculator;
import com.stock.flink.model.DailyPoint;
import com.stock.flink.model.ValidatedStockDailyEvent;
import com.stock.flink.operator.ProcessingClock;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.flink.api.common.functions.OpenContext;
import org.apache.flink.api.common.state.*;
import org.apache.flink.streaming.api.functions.KeyedProcessFunction;
import org.apache.flink.util.Collector;
import java.util.*;

/** Bounded seed+tail state. Repair-blocked keys have no unsafe reset/ready shortcut. */
public final class GenerationIndicatorProcessFunction extends KeyedProcessFunction<String, GenerationInputRecord, GenerationOutputEnvelope> {
    private final IndicatorGenerationBinding binding;
    private final ProcessingClock clock;
    private transient ListState<String> observations;
    private transient ValueState<Boolean> seedComplete;
    private transient ValueState<Boolean> repairBlocked;
    private transient ObjectMapper mapper;
    private transient GenerationEnvelopeParser parser;
    private transient IndicatorCalculator calculator;

    public GenerationIndicatorProcessFunction(IndicatorGenerationBinding binding, ProcessingClock clock) {
        this.binding = Objects.requireNonNull(binding);
        this.clock = Objects.requireNonNull(clock);
    }

    @Override public void open(OpenContext context) {
        // Explicit String/Boolean serializers avoid JDK-internal/Kryo record state dependencies.
        observations = getRuntimeContext().getListState(new ListStateDescriptor<>("generation-v2-recent20", String.class));
        seedComplete = getRuntimeContext().getState(new ValueStateDescriptor<>("generation-v2-seed-complete", Boolean.class));
        repairBlocked = getRuntimeContext().getState(new ValueStateDescriptor<>("generation-v2-repair-blocked", Boolean.class));
        mapper = GenerationEnvelopeParser.jsonMapper();
        parser = new GenerationEnvelopeParser(binding);
        calculator = new IndicatorCalculator();
    }

    @Override public void processElement(GenerationInputRecord input, Context ctx, Collector<GenerationOutputEnvelope> out) throws Exception {
        parser.validate(input.raw(), input.envelope());
        GenerationInputEnvelope e = input.envelope();
        if (!e.tsCode().equals(ctx.getCurrentKey())) throw invalid("KEY_MISMATCH");
        boolean seeded = Boolean.TRUE.equals(seedComplete.value());
        if (e.operation().equals("SEED_END")) {
            seedComplete.update(true);
            // Repeated barriers cannot bypass a historical repair block.
            out.collect(output(input, Boolean.TRUE.equals(repairBlocked.value()) ? "REPAIR_REQUIRED" : "SEED_COMPLETE",
                    Boolean.TRUE.equals(repairBlocked.value()) ? "KEY_REPAIR_BLOCKED" : null, null));
            return;
        }
        List<GenerationInputEnvelope> window = new ArrayList<>(20);
        for (String value : observations.get()) window.add(mapper.readValue(value, GenerationInputEnvelope.class));
        GenerationInputEnvelope latest = window.isEmpty() ? null : window.getLast();
        if (e.operation().equals("SNAPSHOT")) {
            if (seeded) throw invalid("SNAPSHOT_AFTER_SEED_END");
            if (latest != null && !e.tradeDate().isAfter(latest.tradeDate())) {
                if (window.contains(e)) return;
                // Replays outside recent20 are intentionally ambiguous and fail the generation closed.
                throw invalid("SEED_ORDER_OR_REVISION_CONFLICT");
            }
            append(window, e);
            save(window);
            emitIndicator(input, window, out);
            return;
        }
        if (!seeded) throw invalid("TAIL_BEFORE_SEED_END");
        String reason = Boolean.TRUE.equals(repairBlocked.value()) ? "KEY_REPAIR_BLOCKED"
                : e.operation().equals("DELETE") ? "DELETE_REQUIRES_REPAIR"
                : latest != null && e.tradeDate().isBefore(latest.tradeDate()) ? "HISTORICAL_MUTATION" : null;
        if (reason != null) {
            repairBlocked.update(true);
            // Same transactional Kafka sink as indicators: every subsequent live record retains evidence.
            out.collect(output(input, "REPAIR_REQUIRED", reason, null));
            return;
        }
        if (latest != null && e.tradeDate().equals(latest.tradeDate())) {
            int revisionOrder = Long.compare(e.sourceRevision(), latest.sourceRevision());
            if (revisionOrder < 0) throw invalid("STALE_SOURCE_REVISION");
            if (revisionOrder == 0) {
                if (Objects.equals(e.event(), latest.event())) return;
                throw invalid("CONFLICTING_SOURCE_REVISION");
            }
            window.set(window.size() - 1, e);
        } else {
            append(window, e);
        }
        save(window);
        emitIndicator(input, window, out);
    }

    private void emitIndicator(GenerationInputRecord input, List<GenerationInputEnvelope> window,
            Collector<GenerationOutputEnvelope> out) {
        var points = window.stream().map(v -> DailyPoint.from(v.event())).toList();
        out.collect(output(input, "INDICATOR", null, calculator.calculate(points,
                new ValidatedStockDailyEvent(input.raw(), input.envelope().event()), clock.now())));
    }

    private static void append(List<GenerationInputEnvelope> window, GenerationInputEnvelope e) {
        window.add(e);
        if (window.size() > 20) window.removeFirst();
    }

    private void save(List<GenerationInputEnvelope> window) throws Exception {
        List<String> encoded = new ArrayList<>(window.size());
        for (GenerationInputEnvelope e : window) encoded.add(mapper.writeValueAsString(e));
        observations.update(encoded);
    }

    private GenerationOutputEnvelope output(GenerationInputRecord i, String kind, String reason,
            com.stock.common.model.StockDailyIndicatorEvent indicator) {
        var e = i.envelope(); var raw = i.raw();
        return new GenerationOutputEnvelope(binding.generation(), 2, kind, e.tsCode(), e.tradeDate(),
                e.sourceRevision(), indicator, reason, raw.topic(), raw.partition(), raw.offset());
    }

    private static IllegalArgumentException invalid(String reason) {
        return new IllegalArgumentException("Generation state rejected: " + reason);
    }
}
