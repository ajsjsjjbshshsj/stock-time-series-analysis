package com.stock.flink.config;

import org.apache.flink.util.ParameterTool;

public record FlinkJobConfig(
        String bootstrapServers,
        String inputTopic,
        String outputTopic,
        String lateTopic,
        String deadLetterTopic,
        String consumerGroup,
        String checkpointUri,
        long checkpointIntervalMs,
        long checkpointTimeoutMs,
        long checkpointMinPauseMs,
        long kafkaTransactionTimeoutMs,
        int parallelism,
        int supportedSchemaVersion
) {
    public static FlinkJobConfig fromArgs(String[] args) {
        ParameterTool parameters = ParameterTool.fromArgs(args);
        return new FlinkJobConfig(
                value(parameters, "bootstrap-servers", "FLINK_KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"),
                value(parameters, "input-topic", "FLINK_INPUT_TOPIC", "stock.ods.daily.v1"),
                value(parameters, "output-topic", "FLINK_OUTPUT_TOPIC", "stock.dws.daily-indicator.v1"),
                value(parameters, "late-topic", "FLINK_LATE_TOPIC", "stock.late.daily.v1"),
                value(parameters, "dead-letter-topic", "FLINK_DLT_TOPIC", "stock.flink.dead-letter.v1"),
                value(parameters, "group-id", "FLINK_CONSUMER_GROUP", "stock-flink-daily-indicator-v1"),
                value(parameters, "checkpoint-uri", "FLINK_CHECKPOINT_URI", "file:///opt/flink/checkpoints"),
                positive(parameters.getLong("checkpoint-interval-ms", 10_000L), "checkpoint-interval-ms"),
                positive(parameters.getLong("checkpoint-timeout-ms", 60_000L), "checkpoint-timeout-ms"),
                positive(parameters.getLong("checkpoint-min-pause-ms", 5_000L), "checkpoint-min-pause-ms"),
                positive(parameters.getLong("kafka-transaction-timeout-ms", 600_000L), "kafka-transaction-timeout-ms"),
                positiveInt(parameters.getInt("parallelism", 3), "parallelism"),
                positiveInt(parameters.getInt("schema-version", 1), "schema-version")
        );
    }

    private static String value(ParameterTool parameters, String arg, String env, String fallback) {
        return parameters.has(arg) ? parameters.getRequired(arg) : System.getenv().getOrDefault(env, fallback);
    }

    private static long positive(long value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }

    private static int positiveInt(int value, String name) {
        if (value <= 0) {
            throw new IllegalArgumentException(name + " must be positive");
        }
        return value;
    }
}
