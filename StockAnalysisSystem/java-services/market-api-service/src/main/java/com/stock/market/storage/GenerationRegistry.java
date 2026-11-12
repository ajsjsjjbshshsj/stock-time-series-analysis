package com.stock.market.storage;

import com.stock.common.model.IndicatorGenerationBinding;
import org.springframework.dao.DataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Repository;
import java.util.Set;

/** Lazy read-only lookup: construction never queries metadata or creates tables. */
@Repository
public class GenerationRegistry {
    private final JdbcTemplate jdbc;
    private static final Set<String> STATES = Set.of("SEEDING", "SEEDED", "VERIFYING", "VERIFIED", "ACTIVE");

    public GenerationRegistry(JdbcTemplate jdbc) { this.jdbc = jdbc; }

    public IndicatorGenerationBinding require(String generation) {
        new IndicatorGenerationBinding(generation, 1);
        try {
            var rows = jdbc.queryForList("SELECT id,input_topic,output_topic,partitions,state FROM indicator_generation WHERE id = ?", generation);
            if (rows.size() != 1) throw invalid();
            var row = rows.getFirst();
            if (!(row.get("partitions") instanceof Number count)
                    || row.get("state") == null || !STATES.contains(row.get("state"))) throw invalid();
            var binding = new IndicatorGenerationBinding(generation, count.intValue());
            if (!generation.equals(row.get("id")) || !binding.inputTopic().equals(row.get("input_topic"))
                    || !binding.outputTopic().equals(row.get("output_topic"))) throw invalid();
            return binding;
        } catch (DataAccessException ex) {
            throw new DependencyException();
        }
    }

    private static IllegalArgumentException invalid() { return new IllegalArgumentException("Unregistered or unavailable indicator generation"); }
}
