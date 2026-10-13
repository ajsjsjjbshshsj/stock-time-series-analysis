package com.stock.flink.validation;

import com.stock.common.model.StockDailyEvent;
import com.stock.common.validation.StockDailyEventRules;
import java.util.List;

/** Flink adapter; parser retains KEY_MISMATCH versus VALIDATION routing. */
public final class StockDailyEventValidator {
    public List<String> violations(String kafkaKey, StockDailyEvent event, int supportedSchemaVersion) {
        return StockDailyEventRules.violations(kafkaKey, event, supportedSchemaVersion);
    }
}
