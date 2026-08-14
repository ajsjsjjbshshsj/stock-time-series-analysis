package com.stock.consumer.monitoring;

import org.junit.jupiter.api.Test;

import java.time.OffsetDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;

class ConsumerErrorStoreTest {

    @Test
    void keepsNewestErrorsWithinConfiguredCapacity() {
        ConsumerErrorStore store = new ConsumerErrorStore(2);

        store.add(error("first", 1));
        store.add(error("second", 2));
        store.add(error("third", 3));

        assertEquals(2, store.recent().size());
        assertEquals("third", store.recent().get(0).message());
        assertEquals("second", store.recent().get(1).message());
    }

    @Test
    void evictsItemOneWhenItemOneHundredAndOneIsAdded() {
        ConsumerErrorStore store = new ConsumerErrorStore(100);
        for (int index = 1; index <= 101; index++) {
            store.add(error("error-" + index, index));
        }

        assertEquals(100, store.recent().size());
        assertEquals("error-101", store.recent().get(0).message());
        assertEquals("error-2", store.recent().get(99).message());
        assertThrows(UnsupportedOperationException.class, () -> store.recent().clear());
    }

    @Test
    void errorContractDoesNotExposeRawPayload() {
        assertFalse(java.util.Arrays.stream(ConsumerError.class.getRecordComponents())
                .anyMatch(component -> component.getName().toLowerCase().contains("payload")
                        || component.getName().toLowerCase().contains("value")));
    }

    private static ConsumerError error(String message, long offset) {
        return new ConsumerError(OffsetDateTime.parse("2026-08-14T09:00:00+08:00"),
                "VALIDATION", message, "stock.ods.daily.v1", "000001.SZ", 0, offset);
    }
}
