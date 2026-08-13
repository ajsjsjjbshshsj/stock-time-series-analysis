package com.stock.consumer.daily;

import com.stock.common.model.StockDailyEvent;
import org.springframework.stereotype.Component;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** Applies the protocol checks before an event can enter downstream processing. */
@Component
public final class StockDailyEventValidator {

    public void validate(String kafkaKey, StockDailyEvent event) {
        List<String> violations = new ArrayList<>();
        requireText(event.eventId(), "eventId", violations);
        requireText(event.traceId(), "traceId", violations);
        requireText(event.tsCode(), "tsCode", violations);
        requireValue(event.tradeDate(), "tradeDate", violations);
        requireValue(event.open(), "open", violations);
        requireValue(event.high(), "high", violations);
        requireValue(event.low(), "low", violations);
        requireNonNegative(event.close(), "close", violations);
        requireValue(event.preClose(), "preClose", violations);
        requireNonNegative(event.volume(), "volume", violations);
        requireNonNegative(event.amount(), "amount", violations);
        requireText(event.source(), "source", violations);
        requireValue(event.eventTime(), "eventTime", violations);
        requireValue(event.ingestTime(), "ingestTime", violations);

        if (!Objects.equals(1, event.schemaVersion())) {
            violations.add("schemaVersion must equal 1");
        }
        if (kafkaKey == null || !Objects.equals(kafkaKey, event.tsCode())) {
            violations.add("kafkaKey must equal tsCode");
        }
        if (!violations.isEmpty()) {
            throw new StockDailyEventValidationException(violations);
        }
    }

    private static void requireText(String value, String field, List<String> violations) {
        if (value == null || value.isBlank()) {
            violations.add(field + " must not be blank");
        }
    }

    private static void requireValue(Object value, String field, List<String> violations) {
        if (value == null) {
            violations.add(field + " must not be null");
        }
    }

    private static void requireNonNegative(BigDecimal value, String field, List<String> violations) {
        if (value == null) {
            violations.add(field + " must not be null");
        } else if (value.signum() < 0) {
            violations.add(field + " must be greater than or equal to zero");
        }
    }
}
