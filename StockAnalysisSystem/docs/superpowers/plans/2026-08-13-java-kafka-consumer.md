# Java Kafka Consumer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production-shaped Java consumer for `stock.ods.daily.v1` that strictly deserializes and validates `StockDailyEvent`, isolates bad records in `stock.dead-letter.v1`, exposes metrics, and manually acknowledges completed records.

**Architecture:** The Kafka listener consumes raw strings so malformed JSON remains available for dead-letter publication. Focused deserializer, validator, metrics, and dead-letter components are coordinated by one listener; successful processing or successful dead-letter publication is the only path that acknowledges the source record.

**Tech Stack:** Java 21, Spring Boot 4.1, Spring Kafka, Jackson, Micrometer, JUnit 5, Mockito, Embedded Kafka.

## Global Constraints

- Input topic defaults to `stock.ods.daily.v1`; dead-letter topic defaults to `stock.dead-letter.v1`.
- Consumer group defaults to `stock-analysis-daily-v1` and bootstrap servers to `localhost:9092`.
- Consume raw `String` values and reject unknown JSON fields.
- Validate every required V0.3 field, `schemaVersion == 1`, Kafka key equality, and non-negative `close`, `volume`, and `amount`.
- `change` and `pctChg` remain nullable.
- Disable auto commit and use `MANUAL_IMMEDIATE` acknowledgment.
- Never acknowledge a record when dead-letter publication fails.
- Do not add MySQL, ClickHouse, indicator calculation, cleaning, or deduplication.
- Do not commit `.idea` or the repository-local `Java/` JDK directory.

---

### Task 1: Strict Event Deserialization

**Files:**
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/StockDailyEventDeserializer.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/StockDailyEventDeserializerTest.java`

**Interfaces:**
- Consumes: raw JSON `String`.
- Produces: `StockDailyEvent deserialize(String payload)`; throws Jackson processing exceptions for invalid input.

- [ ] **Step 1: Write failing tests** for the shared example, malformed JSON, and an unknown field. The success assertion must verify `tradeDate`, `eventTime`, and exact `BigDecimal` values.
- [ ] **Step 2: Run** `mvn -pl kafka-consumer-service -am -Dtest=StockDailyEventDeserializerTest -Dsurefire.failIfNoSpecifiedTests=false test` and confirm compilation fails because the deserializer does not exist.
- [ ] **Step 3: Implement** a Spring component whose constructor receives `ObjectMapper`, copies it, registers `JavaTimeModule`, enables `FAIL_ON_UNKNOWN_PROPERTIES`, disables timestamp date output and timezone adjustment, and calls `readValue(payload, StockDailyEvent.class)`.
- [ ] **Step 4: Re-run the focused test** and require all three cases to pass.

### Task 2: V0.3 Protocol Validation

**Files:**
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/StockDailyEventValidationException.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/StockDailyEventValidator.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/StockDailyEventValidatorTest.java`

**Interfaces:**
- Consumes: `String kafkaKey`, `StockDailyEvent event`.
- Produces: `void validate(String kafkaKey, StockDailyEvent event)`; throws `StockDailyEventValidationException` containing all detected violation messages.

- [ ] **Step 1: Write a parameterized failing test** that creates a valid event then independently invalidates `eventId`, `traceId`, `tsCode`, `tradeDate`, `open`, `high`, `low`, `close`, `preClose`, `volume`, `amount`, `source`, `eventTime`, `ingestTime`, and `schemaVersion`; add focused cases for key mismatch and negative `close`, `volume`, and `amount`.
- [ ] **Step 2: Run the focused test** and confirm it fails because validator types are absent.
- [ ] **Step 3: Implement minimal validation** using a `List<String>` of violations. Blank strings use `value == null || value.isBlank()`; required numeric fields reject null; only the three protocol-defined numeric fields reject negative values; throw once with an immutable violation list.
- [ ] **Step 4: Re-run** and require every valid/invalid case to pass, including nullable `change` and `pctChg`.

### Task 3: Dead-Letter Publication and Metrics

**Files:**
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/DeadLetterPublisher.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/ConsumerMetrics.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/DeadLetterPublisherTest.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/ConsumerMetricsTest.java`

**Interfaces:**
- `CompletableFuture<SendResult<String, String>> publish(ConsumerRecord<String, String> source, String errorType, String errorMessage)`.
- `void recordSuccess()` and `void recordFailure(String reason)`.

- [ ] **Step 1: Write failing tests** verifying the dead-letter `ProducerRecord` topic, key, raw value, and all five `x-*` headers, plus Micrometer success/failure counter increments.
- [ ] **Step 2: Run the focused tests** and verify failure because the two components do not exist.
- [ ] **Step 3: Implement DeadLetterPublisher** with `KafkaTemplate<String, String>` and configured topic; UTF-8 encode headers and call `kafkaTemplate.send(producerRecord)`.
- [ ] **Step 4: Implement ConsumerMetrics** using counters named `stock.kafka.consumer.success` and `stock.kafka.consumer.failure`, with the failure counter tagged by `reason`.
- [ ] **Step 5: Re-run focused tests** and require them to pass.

### Task 4: Listener Orchestration and Kafka Configuration

**Files:**
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/java/com/stock/consumer/daily/StockDailyEventConsumer.java`
- Modify: `StockAnalysisSystem/java-services/kafka-consumer-service/src/main/resources/application.properties`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/StockDailyEventConsumerTest.java`

**Interfaces:**
- Kafka method: `void consume(ConsumerRecord<String, String> record, Acknowledgment acknowledgment)`.
- Listener topic expression: `${stock.kafka.daily-topic}` and group `${spring.kafka.consumer.group-id}`.

- [ ] **Step 1: Write failing orchestration tests** proving a valid record is deserialized, validated, counted, and acknowledged without DLT publication; deserialization and validation failures publish the original record and acknowledge only after the future succeeds; a failed DLT future increments `dead_letter_publish`, does not acknowledge, and propagates an exception.
- [ ] **Step 2: Run focused tests** and verify failure because the listener is absent.
- [ ] **Step 3: Implement the listener** with separate catches for Jackson and validation failures, a shared dead-letter helper that joins the send future, logs record coordinates, and acknowledges only after success.
- [ ] **Step 4: Configure** String deserializers, disabled auto commit, earliest reset, manual-immediate ack mode, environment-overridable topics/group/bootstrap server, actuator health/info/metrics exposure, and listener disabled by property only in tests that need isolated context.
- [ ] **Step 5: Re-run focused tests and application context test** and require them to pass.

### Task 5: Embedded Kafka End-to-End Verification and Documentation

**Files:**
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/src/test/java/com/stock/consumer/daily/StockDailyEventConsumerIntegrationTest.java`
- Create: `StockAnalysisSystem/java-services/kafka-consumer-service/README.md`

**Interfaces:**
- Produces observable behavior on `stock.ods.daily.v1` and `stock.dead-letter.v1`.

- [ ] **Step 1: Write an Embedded Kafka integration test** with isolated topic names that sends one valid example and one invalid payload, waits for listener metrics, consumes the DLT record, and asserts original key/value and error headers.
- [ ] **Step 2: Run the integration test** and confirm it fails before the complete wiring/configuration works.
- [ ] **Step 3: Make only the wiring changes required** for the embedded-broker test to pass; do not add persistence or business processing.
- [ ] **Step 4: Write README commands** for Maven tests, Docker Compose startup, service startup, sample message production, DLT inspection, and Actuator metric inspection; keep each shell command on one line.
- [ ] **Step 5: Run full verification:** `mvn test` from `StockAnalysisSystem/java-services`, then start the existing Docker Compose Kafka and run the packaged consumer against it if Docker is available.
- [ ] **Step 6: Review staged paths** and commit only Consumer-stage source, tests, configuration, README, and this plan. Leave `.idea` and `Java/` untouched.
