package com.stock.consumer.daily;

import java.util.List;

/** Indicates that a deserialized event does not satisfy the V0.3 protocol. */
public final class StockDailyEventValidationException extends RuntimeException {

    private final List<String> violations;

    public StockDailyEventValidationException(List<String> violations) {
        super(String.join("; ", violations));
        this.violations = List.copyOf(violations);
    }

    public List<String> violations() {
        return violations;
    }
}
