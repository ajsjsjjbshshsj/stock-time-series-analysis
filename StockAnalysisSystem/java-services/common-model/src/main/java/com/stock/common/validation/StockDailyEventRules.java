package com.stock.common.validation;

import com.stock.common.model.StockDailyEvent;
import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;
import java.util.Objects;

/** Transport-independent V1 acceptance rules; adapters own failure classification. */
public final class StockDailyEventRules {
    private StockDailyEventRules() { }

    public static boolean unsupportedSchema(StockDailyEvent event, int supportedVersion) {
        return event != null && event.schemaVersion() != null
                && event.schemaVersion() != supportedVersion;
    }

    public static List<String> violations(String kafkaKey, StockDailyEvent event, int supportedVersion) {
        List<String> errors = new ArrayList<>();
        if (event == null) {
            errors.add("event must not be null");
            return errors;
        }
        text(event.eventId(), "eventId", errors);
        text(event.traceId(), "traceId", errors);
        text(event.tsCode(), "tsCode", errors);
        required(event.tradeDate(), "tradeDate", errors);
        required(event.open(), "open", errors);
        required(event.high(), "high", errors);
        required(event.low(), "low", errors);
        nonNegative(event.close(), "close", errors);
        required(event.preClose(), "preClose", errors);
        nonNegative(event.volume(), "volume", errors);
        nonNegative(event.amount(), "amount", errors);
        text(event.source(), "source", errors);
        required(event.eventTime(), "eventTime", errors);
        required(event.ingestTime(), "ingestTime", errors);
        required(event.schemaVersion(), "schemaVersion", errors);
        if (unsupportedSchema(event, supportedVersion)) {
            errors.add("unsupported schemaVersion");
        }
        if (kafkaKey == null || !Objects.equals(kafkaKey, event.tsCode())) {
            errors.add("kafkaKey must equal tsCode");
        }
        // change/pctChg are optional. Do not add stronger price/date/code rules here.
        return errors;
    }

    private static void text(String value, String name, List<String> errors) {
        if (value == null || value.isBlank()) errors.add(name + " must not be blank");
    }

    private static void required(Object value, String name, List<String> errors) {
        if (value == null) errors.add(name + " must not be null");
    }

    private static void nonNegative(BigDecimal value, String name, List<String> errors) {
        if (value == null) errors.add(name + " must not be null");
        else if (value.signum() < 0) errors.add(name + " must be greater than or equal to zero");
    }
}
