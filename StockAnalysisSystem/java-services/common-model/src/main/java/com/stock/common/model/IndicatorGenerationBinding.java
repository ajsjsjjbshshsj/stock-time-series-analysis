package com.stock.common.model;

/** Immutable transport namespace for a V2 indicator generation.
 * Matching a namespace is not proof of bootstrap completion or readiness.
 * Partition count must also be checked against broker metadata by the adapter.
 */
public record IndicatorGenerationBinding(String generation, int partitions) {
    public IndicatorGenerationBinding {
        if (generation == null || !generation.matches("[0-9a-f]{32}")) {
            throw new IllegalArgumentException("Invalid indicator generation");
        }
        if (partitions < 1 || partitions > 256) {
            throw new IllegalArgumentException("Invalid indicator partition count");
        }
    }

    public String inputTopic() {
        return "stock.indicators." + generation + ".raw.v2";
    }

    public String outputTopic() {
        return "stock.indicators." + generation + ".daily.v2";
    }

    public String consumerGroup() {
        return "stock-indicators-" + generation + "-v2";
    }

    public void requireInput(String topic, int partition, String recordGeneration) {
        require(inputTopic(), topic, partition, recordGeneration);
    }

    public void requireOutput(String topic, int partition, String recordGeneration) {
        require(outputTopic(), topic, partition, recordGeneration);
    }

    private void require(String expected, String actual, int partition, String recordGeneration) {
        if (!expected.equals(actual) || !generation.equals(recordGeneration)
                || partition < 0 || partition >= partitions) {
            throw new IllegalArgumentException("Indicator generation transport mismatch");
        }
    }
}
