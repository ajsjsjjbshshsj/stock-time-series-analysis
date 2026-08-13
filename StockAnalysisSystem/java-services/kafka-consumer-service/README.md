# Kafka Consumer Service

该服务消费 `stock.ods.daily.v1` 中的原始 JSON，转换为 `StockDailyEvent` 并执行 V0.3 协议校验。合法消息确认 Offset；非法消息保留原 Key 和 JSON，附带错误 headers 后发送到 `stock.dead-letter.v1`。

## 本地要求

- Java 21
- Maven 3.9+
- Docker Desktop

## 运行测试

在 `StockAnalysisSystem/java-services` 目录执行：

```powershell
mvn test
```

测试包含反序列化、协议校验、Offset 编排、死信发布、指标以及 Embedded Kafka 端到端验证。

## 启动 Kafka

在 `StockAnalysisSystem` 目录执行：

```powershell
docker compose -f infrastructure\docker-compose.yml up -d
```

查看容器：

```powershell
docker compose -f infrastructure\docker-compose.yml ps
```

## 启动 Consumer

在 `StockAnalysisSystem/java-services` 目录执行：

```powershell
mvn -pl kafka-consumer-service -am spring-boot:run
```

默认配置可以通过环境变量覆盖：`KAFKA_BOOTSTRAP_SERVERS`、`KAFKA_CONSUMER_GROUP`、`STOCK_DAILY_TOPIC`、`STOCK_DEAD_LETTER_TOPIC`。

## 发送协议示例消息

在 `StockAnalysisSystem` 目录执行以下单行命令，然后粘贴 JSON，按 `Ctrl+Z` 和回车结束输入：

```powershell
docker compose -f infrastructure\docker-compose.yml exec kafka /opt/kafka/bin/kafka-console-producer.sh --bootstrap-server localhost:9092 --topic stock.ods.daily.v1 --property parse.key=true --property key.separator=:
```

输入格式为 `KafkaKey:JSON`，例如：

```text
000001.SZ:{"eventId":"TUSHARE:000001.SZ:20260703","traceId":"daily-tushare-20260703-10001","tsCode":"000001.SZ","tradeDate":"2026-07-03","open":10.25,"high":10.68,"low":10.12,"close":10.55,"preClose":10.20,"change":0.35,"pctChg":3.4314,"volume":1250345.00,"amount":13054890.25,"source":"TUSHARE","eventTime":"2026-07-03T15:00:00+08:00","ingestTime":"2026-07-03T16:20:30+08:00","schemaVersion":1}
```

## 查看死信消息

```powershell
docker compose -f infrastructure\docker-compose.yml exec kafka /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic stock.dead-letter.v1 --from-beginning --property print.key=true --property print.headers=true
```

也可以访问 [Kafka UI](http://localhost:8081/) 查看 Topic。

## 查看指标

服务启动后访问：

- `http://localhost:8080/actuator/metrics/stock.kafka.consumer.success`
- `http://localhost:8080/actuator/metrics/stock.kafka.consumer.failure`

失败指标使用 `reason` 标签区分 `deserialization`、`validation` 和 `dead_letter_publish`。
