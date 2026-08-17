package com.stock.consumer.monitoring;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

/** Bounded, newest-first in-memory store for recent consumer errors. */
@Service
public final class ConsumerErrorStore {

    private final int capacity;
    private final ArrayDeque<ConsumerError> records = new ArrayDeque<>();

    public ConsumerErrorStore(@Value("${stock.monitor.error-capacity:100}") int capacity) {
        if (capacity < 1) {
            throw new IllegalArgumentException("capacity must be greater than zero");
        }
        this.capacity = capacity;
    }

    public synchronized void add(ConsumerError record) {
        records.addFirst(record);
        while (records.size() > capacity) {
            records.removeLast();
        }
    }

    public synchronized List<ConsumerError> recent() {
        return List.copyOf(new ArrayList<>(records));
    }
}
