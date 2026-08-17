# StockAnalysisSystem V0.3

V0.3 在原 Python 股票项目上增加了可回滚的消息链路：Python 继续负责采集和 MySQL 写入，同时可把标准事件发送到 Kafka；Java 负责消费、协议校验、异常隔离与运行监控。

```text
Tushare/AkShare -> Python Collector -> MySQL
                                  \-> Kafka -> Java Consumer -> DLT / Monitoring API / /monitor/
```

## 目录

```text
StockAnalysisSystem/
├── python-services/
│   ├── python-collector/       # 采集、事件工厂、MySQL/Kafka/Dual Output
│   └── stock-analysis-app/    # 原分析、训练和预测代码
├── java-services/
│   ├── common-model/          # StockDailyEvent / StockBasicEvent 公共契约
│   └── kafka-consumer-service/# 消费、校验、DLT、API 和监控页
├── infrastructure/            # Kafka、Topic 初始化、Kafka UI
├── scripts/                   # smoke 与对账脚本
└── docs/                      # 协议、测试、对账和发布说明
```

## 启动

复制 `.env.example` 为本地 `.env` 并填写自己的 Token/数据库密码，真实 `.env` 不得提交。

在 `StockAnalysisSystem` 启动 Kafka：

```powershell
docker compose -f infrastructure\docker-compose.yml up -d
```

在 `StockAnalysisSystem\java-services` 启动 Java Consumer：

```powershell
$env:JAVA_HOME='C:\path\to\jdk-21'; $env:Path="$env:JAVA_HOME\bin;$env:Path"; mvn -pl kafka-consumer-service -am spring-boot:run
```

在 `StockAnalysisSystem\python-services\python-collector` 运行采集器：

```powershell
D:\Python\python.exe app\main.py --help
```

输出模式：`COLLECTOR_OUTPUT_MODE=mysql`、`kafka` 或 `dual`。默认 `mysql`；Kafka 出现问题时将其改回 `mysql` 并重启 Collector 即可回滚到 V0.2 数据路径。

## 页面与接口

- 消费监控：[http://localhost:8080/monitor/](http://localhost:8080/monitor/)
- 健康检查：[http://localhost:8080/actuator/health](http://localhost:8080/actuator/health)
- 消费统计：[http://localhost:8080/api/consumer/statistics](http://localhost:8080/api/consumer/statistics)
- 最近错误：[http://localhost:8080/api/consumer/errors](http://localhost:8080/api/consumer/errors)
- Kafka UI：[http://localhost:8081/](http://localhost:8081/)

监控数据仅保存在当前 Java 进程内存中，服务重启后从零开始；最近错误不保存完整 JSON payload。

## 测试与验收

```powershell
cd python-services\python-collector; D:\Python\python.exe -m pytest -v
```

```powershell
cd java-services; $env:JAVA_HOME='C:\path\to\jdk-21'; $env:Path="$env:JAVA_HOME\bin;$env:Path"; mvn test
```

Kafka 和 Java 启动后执行有效链路及异常隔离：

```powershell
D:\Python\python.exe scripts\verify_v03_smoke.py --scenario valid --count 1
```

```powershell
D:\Python\python.exe scripts\verify_v03_smoke.py --scenario all --count 20
```

确定性对账：

```powershell
D:\Python\python.exe scripts\reconcile_v03.py --trace-id <traceId> --collector-count <n> --kafka-success <n> --kafka-failure <n> --java-success <n> --java-isolated-failure <n> --dlt-count <n>
```

详细结果见 [V0.3_TEST_CASES.md](docs/V0.3_TEST_CASES.md)、[V0.3_RECONCILIATION_REPORT.md](docs/V0.3_RECONCILIATION_REPORT.md) 和 [V0.3_RELEASE_NOTES.md](docs/V0.3_RELEASE_NOTES.md)。
