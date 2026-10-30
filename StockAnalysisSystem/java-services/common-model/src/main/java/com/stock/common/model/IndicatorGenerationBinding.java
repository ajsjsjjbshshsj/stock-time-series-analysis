package com.stock.common.model;

/** Immutable transport namespace for a V2 indicator generation.
 * Matching a namespace is not proof of bootstrap completion or readiness.
 * Partition count must also be checked against broker metadata by the adapter.
 */
public record IndicatorGenerationBinding(String generation, int partitions) implements java.io.Serializable {
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

    /** Kafka's default keyed partition rule, pinned independently of broker expansion. */
    public int partitionFor(String tsCode) {
        if (tsCode == null || tsCode.isBlank()) throw new IllegalArgumentException("Missing stock key");
        byte[] data = tsCode.getBytes(java.nio.charset.StandardCharsets.UTF_8);
        int h = 0x9747b28c ^ data.length;
        int m = 0x5bd1e995;
        int position = 0;
        while (data.length - position >= 4) {
            int k = (data[position] & 0xff) | ((data[position + 1] & 0xff) << 8)
                    | ((data[position + 2] & 0xff) << 16) | ((data[position + 3] & 0xff) << 24);
            k *= m; k ^= k >>> 24; k *= m;
            h = h * m ^ k;
            position += 4;
        }
        int remaining = data.length - position;
        if (remaining >= 3) h ^= (data[position + 2] & 0xff) << 16;
        if (remaining >= 2) h ^= (data[position + 1] & 0xff) << 8;
        if (remaining >= 1) { h ^= data[position] & 0xff; h *= m; }
        h ^= h >>> 13; h *= m; h ^= h >>> 15;
        return (h & 0x7fffffff) % partitions;
    }

    /** Compare the actual broker count, not only a received record's upper bound. */
    public void requirePartitionCount(String topic, int actualCount) {
        if ((!inputTopic().equals(topic) && !outputTopic().equals(topic)) || actualCount != partitions) {
            throw new IllegalArgumentException("Indicator generation broker partition count mismatch");
        }
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
