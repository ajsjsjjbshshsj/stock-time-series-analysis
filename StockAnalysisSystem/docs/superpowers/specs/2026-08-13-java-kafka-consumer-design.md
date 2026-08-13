# Java Kafka Consumer 设计

## 1. 目标

在 `kafka-consumer-service` 中实现 V0.3 日线事件消费链路：

```text
stock.ods.daily.v1
  -> JSON 反序列化
  -> StockDailyEvent
  -> 协议校验
  -> 成功处理或死信隔离
  -> 手动确认 Offset
```

本阶段交付一个可运行、可测试、可观察并能隔离异常消息的 Consumer。

## 2. 职责边界

本阶段负责：

- 消费 `stock.ods.daily.v1`。
- 将消息 JSON 严格反序列化为公共模型 `StockDailyEvent`。
- 执行 `V0.3_MESSAGE_SCHEMA.md` 第 9 节规定的全部校验。
- 将反序列化失败或协议校验失败的消息发送到 `stock.dead-letter.v1`。
- 记录成功、失败指标和消息定位信息。
- 处理完成后手动确认 Kafka Offset。

本阶段不负责：

- 写入 MySQL 或 ClickHouse。
- 技术指标计算、行情清洗和业务去重。
- 消费 `stock.ods.basic.v1`。

## 3. 组件设计

### 3.1 StockDailyEventConsumer

Kafka 入口，接收原始 `ConsumerRecord<String, String>` 和 `Acknowledgment`。它负责协调反序列化、校验、指标、死信发送与 Offset 确认，不承载具体校验规则。

### 3.2 StockDailyEventDeserializer

使用配置了 `JavaTimeModule` 的 Jackson `ObjectMapper` 将消息值转换为 `StockDailyEvent`。未知 JSON 字段必须导致反序列化失败，避免生产者在未升级协议版本时静默改变消息结构。

### 3.3 StockDailyEventValidator

返回结构化校验结果，检查：

1. `eventId`、`traceId`、`tsCode`、`source` 非空。
2. `tradeDate`、`eventTime`、`ingestTime` 非空。
3. `schemaVersion == 1`。
4. Kafka Key 等于 `tsCode`。
5. `close`、`volume`、`amount` 非空且大于等于零。

`change` 和 `pctChg` 按协议允许为空。其他价格字段虽然协议标记必填，也需要校验非空；负值限制只应用于协议明确要求的三个字段。

### 3.4 DeadLetterPublisher

通过 `KafkaTemplate<String, String>` 将原始消息发送到 `stock.dead-letter.v1`，保留原 Kafka Key 和原始 JSON。附加 headers：

- `x-original-topic`
- `x-original-partition`
- `x-original-offset`
- `x-error-type`
- `x-error-message`

只有死信发送成功后才确认原消息 Offset。死信发送失败时抛出异常，由 Kafka 容器重新投递原消息，避免静默丢失。

### 3.5 ConsumerMetrics

使用 Micrometer Counter 记录：

- `stock.kafka.consumer.success`
- `stock.kafka.consumer.failure`，按 `reason` 标签区分 `deserialization`、`validation` 和 `dead_letter_publish`。

## 4. 消息处理流程

合法消息：

1. 接收原始 Kafka Record。
2. 反序列化为 `StockDailyEvent`。
3. 校验消息字段与 Kafka Key。
4. 记录成功指标和摘要日志。
5. 手动确认 Offset。

非法消息：

1. 捕获反序列化异常或校验失败结果。
2. 记录 Topic、Partition、Offset、Key 与失败原因。
3. 增加对应失败指标。
4. 将原始消息及错误 headers 发往死信 Topic。
5. 死信发送成功后确认 Offset，继续消费后续消息。

## 5. Kafka 配置

默认本地配置：

- Bootstrap Servers：`${KAFKA_BOOTSTRAP_SERVERS:localhost:9092}`
- Consumer Group：`${KAFKA_CONSUMER_GROUP:stock-analysis-daily-v1}`
- Input Topic：`${STOCK_DAILY_TOPIC:stock.ods.daily.v1}`
- Dead-letter Topic：`${STOCK_DEAD_LETTER_TOPIC:stock.dead-letter.v1}`
- Key/Value Deserializer：`StringDeserializer`
- Auto Commit：关闭
- Ack Mode：`MANUAL_IMMEDIATE`
- Auto Offset Reset：`earliest`

Consumer 主动处理 JSON，而不是让 Spring Kafka 在 Listener 之前反序列化。这样即使 JSON 损坏，也能取得原始消息并写入死信 Topic。

## 6. 错误处理原则

- 单条坏消息不能导致 Consumer 进程退出。
- 任何失败日志都必须包含消息定位信息。
- 只有成功处理或成功进入死信 Topic 的消息才能确认 Offset。
- 死信发布失败不得确认 Offset，以便后续重试。
- 日志不输出完整消息体，避免大量数据或敏感内容污染日志。

## 7. 测试策略

### 单元测试

- 合法 JSON 能反序列化并保留日期、时区和金额精度。
- 未知字段和非法 JSON 被拒绝。
- 逐项覆盖协议校验规则。
- 合法消息确认 Offset 并增加成功指标。
- 无效消息发送死信，成功后确认 Offset。
- 死信发送失败时不确认 Offset并向上抛出异常。

### Kafka 集成测试

使用 Embedded Kafka 验证：

- 合法消息可以被 Listener 消费。
- 非法消息进入 `stock.dead-letter.v1`。
- 死信消息保留原 Key、原 JSON 和错误 headers。

### 本地 Docker 联调

在现有 `infrastructure/docker-compose.yml` 启动 Kafka 后，发送协议示例消息，确认消费日志、Actuator 指标和 Kafka UI 中的死信消息符合预期。

## 8. 完成标准

- Maven 多模块测试全部通过。
- Consumer 能连接本地 Docker Kafka 并消费 `stock.ods.daily.v1`。
- 合法消息被确认且不会进入死信 Topic。
- 非法消息被隔离，Consumer 可继续处理后续消息。
- 成功、失败指标可通过 Actuator 查看。
- 新增代码包含解释职责和关键决策的注释，不添加无意义的逐行注释。
