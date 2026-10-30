package com.stock.market.storage;

import com.stock.common.model.IndicatorGenerationBinding;
import com.stock.market.ingest.GenerationRecord;
import java.time.LocalDate;
import java.util.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.*;
import com.stock.common.model.StockDailyIndicatorEvent;
import com.stock.market.api.QueryParameters;
import com.stock.market.ingest.IndicatorListener;
import org.springframework.stereotype.Repository;

/** Additive generation storage. Never activates a generation or clears repair evidence. */
@Repository
public class GenerationIndicatorRepository {
    public enum KeyStatus { NO_SEED, SEED_COMPLETE, REPAIR_REQUIRED }
    public record QueryResult(KeyStatus status, List<Map<String, Object>> indicators) {}
    private static final int MAX_BATCH = 500, MAX_BYTES = 4 * 1024 * 1024;
    private final ClickHouseClient client;
    private final GenerationRegistry registry;
    private final ObjectMapper json = IndicatorListener.mapper().enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);

    public GenerationIndicatorRepository(ClickHouseClient client, GenerationRegistry registry) {
        this.client = client;
        this.registry = registry;
    }

    public void insertBatch(List<GenerationRecord> records) {
        if (records == null || records.size() > MAX_BATCH) throw new IllegalArgumentException("Invalid generation batch");
        var data = new ArrayList<String>();
        var controls = new ArrayList<String>();
        try {
            // Serialize/validate the entire batch before the first network write.
            for (var record : records) {
                var e = record.envelope();
                var row = new LinkedHashMap<String, Object>();
                if (e.kind().equals("INDICATOR")) {
                    for (var component : StockDailyIndicatorEvent.class.getRecordComponents()) {
                        var value = component.getAccessor().invoke(e.indicator());
                        if (value instanceof java.time.OffsetDateTime time) value = time.toInstant().toString();
                        row.put(component.getName().replaceAll("([a-z0-9])([A-Z])", "$1_$2").toLowerCase(Locale.ROOT), value);
                    }
                } else {
                    row.put("ts_code", e.tsCode());
                    row.put("trade_date", e.tradeDate());
                    row.put("schema_version", e.schemaVersion());
                    row.put("kind", e.kind());
                    row.put("reason", e.reason());
                }
                row.put("generation", e.generation());
                row.put("source_revision", e.sourceRevision());
                row.put("input_topic", e.inputTopic());
                row.put("input_partition", e.inputPartition());
                row.put("input_offset", e.inputOffset());
                row.put("kafka_topic", record.topic());
                row.put("kafka_partition", record.partition());
                row.put("kafka_offset", record.offset());
                row.put("version", record.offset());
                String line = json.writeValueAsString(row) + "\n";
                if (line.getBytes(java.nio.charset.StandardCharsets.UTF_8).length > MAX_BYTES) throw new IllegalArgumentException("Generation row too large");
                (e.kind().equals("INDICATOR") ? data : controls).add(line);
            }
            write("generation_daily_indicators", data);
            write("generation_indicator_events", controls);
        } catch (DependencyException | IllegalArgumentException ex) {
            throw ex;
        } catch (Exception ex) {
            throw new DependencyException();
        }
    }

    private void write(String table, List<String> rows) {
        var batch = new StringBuilder();
        int bytes = 0;
        for (String row : rows) {
            int size = row.getBytes(java.nio.charset.StandardCharsets.UTF_8).length;
            if (bytes + size > MAX_BYTES) {
                executeInsert(table, batch.toString());
                batch.setLength(0);
                bytes = 0;
            }
            batch.append(row);
            bytes += size;
        }
        if (!batch.isEmpty()) executeInsert(table, batch.toString());
    }

    private void executeInsert(String table, String body) {
        if (!client.execute("INSERT INTO " + table + " FORMAT JSONEachRow", Map.of(), body).isBlank()) throw new DependencyException();
    }

    public KeyStatus status(IndicatorGenerationBinding binding, String code) {
        requireBinding(binding, code);
        return readStatus(binding, code);
    }

    private KeyStatus readStatus(IndicatorGenerationBinding binding, String code) {
        try {
            var response = client.execute("SELECT countIf(kind = 'SEED_COMPLETE') AS seed_count, countIf(kind = 'REPAIR_REQUIRED') AS repair_count"
                    + " FROM generation_indicator_events FINAL WHERE generation = {generation:String} AND ts_code = {code:String} FORMAT JSONEachRow",
                    Map.of("generation", binding.generation(), "code", code), "");
            var node = json.readTree(response);
            if (node == null || !node.path("seed_count").canConvertToLong() || !node.path("repair_count").canConvertToLong()
                    || node.get("seed_count").asLong() < 0 || node.get("repair_count").asLong() < 0) throw new DependencyException();
            if (node.get("repair_count").asLong() > 0) return KeyStatus.REPAIR_REQUIRED;
            return node.get("seed_count").asLong() > 0 ? KeyStatus.SEED_COMPLETE : KeyStatus.NO_SEED;
        } catch (Exception ex) {
            throw new DependencyException();
        }
    }

    public QueryResult query(IndicatorGenerationBinding binding, String code, LocalDate start, LocalDate end, int limit) {
        if (start == null || end == null) throw new IllegalArgumentException("Explicit generation date bounds required");
        QueryParameters.series(code, limit, start, end);
        requireBinding(binding, code);
        KeyStatus status = readStatus(binding, code);
        if (status != KeyStatus.SEED_COMPLETE) return new QueryResult(status, List.of());
        try {
            var params = Map.of("generation", binding.generation(), "code", code,
                    "start", start.toString(), "end", end.toString(), "limit", Integer.toString(limit));
            String sql = "SELECT * FROM (SELECT * FROM generation_daily_indicators FINAL"
                    + " WHERE generation = {generation:String} AND ts_code = {code:String}"
                    + " AND trade_date >= {start:Date} AND trade_date <= {end:Date}"
                    + " AND NOT EXISTS (SELECT 1 FROM generation_indicator_events FINAL WHERE generation = {generation:String} AND ts_code = {code:String} AND kind = 'REPAIR_REQUIRED')"
                    + " ORDER BY trade_date DESC LIMIT {limit:UInt32}) ORDER BY trade_date ASC FORMAT JSONEachRow";
            var rows = new ArrayList<Map<String, Object>>();
            for (var line : client.execute(sql, params, "").lines().filter(s -> !s.isBlank()).toList()) {
                rows.add(json.readValue(line, new TypeReference<LinkedHashMap<String, Object>>() {}));
            }
            // Status is per key only; it says nothing about latest coverage or global readiness.
            KeyStatus after = readStatus(binding, code);
            return after == KeyStatus.SEED_COMPLETE ? new QueryResult(after, List.copyOf(rows)) : new QueryResult(after, List.of());
        } catch (Exception ex) {
            throw new DependencyException();
        }
    }

    private void requireBinding(IndicatorGenerationBinding binding, String code) {
        QueryParameters.code(code);
        if (binding == null || !binding.equals(registry.require(binding.generation()))) throw new IllegalArgumentException("Generation binding mismatch");
    }
}
