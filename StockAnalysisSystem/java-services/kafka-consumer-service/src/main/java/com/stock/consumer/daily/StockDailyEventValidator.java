package com.stock.consumer.daily;

import com.stock.common.model.StockDailyEvent;
import com.stock.common.validation.StockDailyEventRules;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import java.util.List;

/** Consumer adapter: shared rules, with the existing version-exception precedence. */
@Component
public final class StockDailyEventValidator {
    private final int supportedSchemaVersion;

    public StockDailyEventValidator(@Value("${stock.kafka.supported-schema-version:1}") int supportedSchemaVersion) {
        this.supportedSchemaVersion = supportedSchemaVersion;
    }

    public void validate(String kafkaKey, StockDailyEvent event) {
        if (StockDailyEventRules.unsupportedSchema(event, supportedSchemaVersion)) {
            throw new UnsupportedSchemaVersionException(event.schemaVersion(), supportedSchemaVersion);
        }
        List<String> violations = StockDailyEventRules.violations(kafkaKey, event, supportedSchemaVersion);
        if (!violations.isEmpty()) throw new StockDailyEventValidationException(violations);
    }
}
