# Kafka Consumer Service

该服务消费 `stock.ods.daily.v1` 原始 JSON，反序列化为公共 `StockDailyEvent`，验证字段、Kafka Key 和协议版本。合法消息计入成功；非法消息保留原 Key/Value 并附带诊断 headers 发送至 `stock.dead-letter.v1`。

## 启动

要求 Java 21、Maven 3.9+ 和可访问的 Kafka。在 `StockAnalysisSystem\java-services` 执行：

```powershell
$env:JAVA_HOME='C:\path\to\jdk-21'; $env:Path="$env:JAVA_HOME\bin;$env:Path"; mvn -pl kafka-consumer-service -am spring-boot:run
```

常用环境变量：

| 变量 | 默认值 |
|---|---|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` |
| `KAFKA_CONSUMER_GROUP` | `stock-analysis-daily-v1` |
| `STOCK_DAILY_TOPIC` | `stock.ods.daily.v1` |
| `STOCK_DEAD_LETTER_TOPIC` | `stock.dead-letter.v1` |
| `STOCK_SUPPORTED_SCHEMA_VERSION` | `1` |
| `STOCK_MONITOR_ERROR_CAPACITY` | `100` |

## 监控

- `/monitor/`：依赖零构建的运维页面，每 5 秒刷新，也支持手动刷新。
- `/api/consumer/statistics`：当前进程累计统计。
- `/api/consumer/errors`：最新优先的有界错误列表。
- `/actuator/health`：Spring Boot 健康状态。
- `/actuator/metrics/stock.kafka.consumer.success` 与 `/actuator/metrics/stock.kafka.consumer.failure`：Micrometer 指标。

统计和错误队列是进程内状态，重启服务会清零。错误记录只包含时间、类型、消息、Topic、Key、Partition 和 Offset，不含完整 payload。

## 测试

```powershell
mvn test
```

测试覆盖严格 JSON、协议版本、Kafka Key、并发统计、错误容量、REST API、静态页面、DLT headers、确认顺序和 Embedded Kafka 端到端链路。
