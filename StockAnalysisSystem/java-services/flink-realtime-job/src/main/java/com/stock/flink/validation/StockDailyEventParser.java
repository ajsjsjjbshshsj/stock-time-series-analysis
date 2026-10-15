package com.stock.flink.validation;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.stock.common.model.StockDailyEvent;
import com.stock.common.validation.StockDailyEventCodec;
import com.stock.flink.model.RawKafkaRecord;
import com.stock.flink.model.ValidatedStockDailyEvent;

import java.util.List;
import java.util.Objects;

public final class StockDailyEventParser {
    private final int supportedSchemaVersion;
    private final StockDailyEventValidator validator = new StockDailyEventValidator();
    private final StockDailyEventCodec codec = new StockDailyEventCodec();

    public StockDailyEventParser(int supportedSchemaVersion) {
        this.supportedSchemaVersion = supportedSchemaVersion;
    }

    public ValidatedStockDailyEvent parse(RawKafkaRecord raw) {
        if (raw.payload() == null) {
            throw new InvalidStockDailyEventException("JSON_PARSE", "invalid StockDailyEvent JSON");
        }
        final StockDailyEvent event;
        try {
            event = codec.decode(raw.payload());
        } catch (JsonProcessingException e) {
            throw new InvalidStockDailyEventException("JSON_PARSE", "invalid StockDailyEvent JSON", e);
        }
        List<String> violations = validator.violations(raw.key(), event, supportedSchemaVersion);
        if (!violations.isEmpty()) {
            String errorType = Objects.equals(raw.key(), event.tsCode()) ? "VALIDATION" : "KEY_MISMATCH";
            throw new InvalidStockDailyEventException(errorType, String.join("; ", violations));
        }
        return new ValidatedStockDailyEvent(raw, event);
    }
}
