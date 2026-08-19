# StockAnalysisSystem V0.4

V0.4 在 V0.3 Kafka 消息链路基础上统一了市场数据入口：所有 Tushare/AkShare SDK 调用集中在 Python Collector，分析应用只读取 MySQL 和兼容缓存。项目保留 MySQL 单写模式，因此 Kafka 故障不会阻断原有采集与分析流程。

```text
Tushare / AkShare
       |
       v
python-collector
  |          |
  v          v
MySQL      Kafka ---> Java Consumer ---> DLT / Monitoring API
  |
  v
stock-analysis-app ---> Streamlit / Features / Models / Results
```

## 服务边界

| 模块 | 职责 | 不负责 |
|---|---|---|
| `python-services/python-collector` | 外部数据采集、标准化、校验、MySQL/Kafka 输出、任务重试 | 特征工程和模型训练 |
| `java-services/kafka-consumer-service` | Kafka 消费、协议校验、DLT、统计 API 和监控页面 | 调用市场数据 SDK |
| `python-services/stock-analysis-app` | MySQL/兼容缓存读取、特征工程、训练、预测和可视化 | 直接调用 Tushare/AkShare |
| `infrastructure` | Kafka、Topic、Kafka UI 和 MySQL 迁移脚本 | 业务分析逻辑 |

## 目录

```text
StockAnalysisSystem/
├── python-services/
│   ├── python-collector/
│   │   ├── app/collectors/       # Tushare/AkShare 采集适配器
│   │   ├── app/market_data/      # 外部 SDK 客户端唯一入口
│   │   ├── app/jobs/             # 日线、daily-basic、成分股任务
│   │   ├── app/outputs/          # MySQL/Kafka/Dual Output
│   │   └── tests/
│   └── stock-analysis-app/
│       ├── data_loader/          # MySQL 数据访问兼容门面
│       ├── data_processor/       # 面板、传统特征和股票池
│       ├── analysis/             # LightGBM、Transformer、预测
│       ├── visualization/        # Streamlit 和绘图
│       └── tests/
├── java-services/
│   ├── common-model/             # StockDailyEvent / StockBasicEvent
│   └── kafka-consumer-service/   # Consumer、DLT、监控 API
├── infrastructure/
│   ├── docker-compose.yml
│   └── mysql/migrations/
├── scripts/                      # 冒烟和对账脚本
└── docs/                         # 协议、测试、发布与迁移报告
```

## 数据库表

| 表 | 所有者/用途 |
|---|---|
| `stock_basic` | Collector 写入的股票基础资料 |
| `stock_daily` | Collector 写入的原始日线 OHLCV |
| `stock_daily_basic` | Collector 写入的原始换手率、估值和总市值 |
| `stock_constituent` | Collector 写入的指数/行业成分股历史快照 |
| `collection_task` | Collector 采集任务幂等、状态和失败重试记录 |
| `stock_features` | Analysis App 生成的传统特征 |
| `analysis_result` | Analysis App 保存的分析和预测结果 |

`stock_features` 和 `analysis_result` 没有废弃。V0.4 只是把原始 daily-basic 与成分股数据从分析逻辑中拆出来，交由 Collector 统一采集。

## 环境准备

建议环境：Python 3.12、JDK 21、Maven 3.9+、MySQL 8、Docker Desktop。

```powershell
Copy-Item .env.example .env
```

至少填写：

```env
TUSHARE_TOKEN=
DB_HOST=localhost
DB_PORT=3306
DB_NAME=stock_analysis
DB_USER=root
DB_PASSWORD=
COLLECTOR_SOURCE=tushare
COLLECTOR_OUTPUT_MODE=mysql
```

真实 Token 和密码只能保存在本地 `.env`，不得提交到 Git。

## 基础设施

Docker Compose 当前启动 Kafka、Topic 初始化任务和 Kafka UI：

```powershell
docker compose -f infrastructure\docker-compose.yml up -d
docker compose -f infrastructure\docker-compose.yml ps
```

检查 Topic：

```powershell
docker compose -f infrastructure\docker-compose.yml exec kafka /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

MySQL 使用本地实例。首次升级 V0.4 时按顺序执行 `infrastructure/mysql/migrations/` 下的迁移脚本；`V0.4_001_market_reference_tables.sql` 会创建 `stock_daily_basic`、`stock_constituent`，并统一与旧行情表兼容的字符排序规则。

## Python Collector

```powershell
cd python-services\python-collector
python -m app.main --help
```

常用命令：

```powershell
# 同一天分别采集 OHLCV 和 daily-basic，两个任务使用独立事务
python -m app.main daily-market --date 20260817

# 只针对 stock_daily 已存在的交易日逐日补采 daily-basic
python -m app.main daily-basic-history --start 20220104 --end 20260817

# 保存指数成分股快照
python -m app.main constituent --type index --code 000300.SH --date 20260817

# 重试当前数据源的失败任务
python -m app.main retry-failed
```

输出模式：

| `COLLECTOR_OUTPUT_MODE` | 行为 |
|---|---|
| `mysql` | 只写 MySQL，默认值和 Kafka 故障回滚模式 |
| `kafka` | 只发送 Kafka；完整分析数据仍依赖已存在的 MySQL 数据 |
| `dual` | 同时写 MySQL 和发送 Kafka |

`daily` 与 `daily-basic` 使用独立任务状态。一个任务失败不会伪装成另一个任务成功；失败记录保留在 `collection_task` 中。

## Java Consumer

```powershell
cd java-services
$env:JAVA_HOME='C:\path\to\jdk-21'
$env:Path="$env:JAVA_HOME\bin;$env:Path"
mvn -pl kafka-consumer-service -am spring-boot:run
```

Java 服务负责：

- 消费 `stock.ods.daily.v1` 和 `stock.ods.basic.v1`。
- 校验 `schemaVersion`、字段类型和事件协议。
- 将无法处理的事件隔离到 `stock.dead-letter.v1`。
- 暴露健康检查、消费统计、最近错误和监控页面。

## Stock Analysis App

分析端的数据入口已经改为 MySQL：

- 日线查询 LEFT JOIN `stock_daily_basic`，缺少 daily-basic 时仍保留 OHLCV 行。
- 指数和行业筛选读取 `stock_constituent` 的最新或指定日期快照。
- 旧 `DataCollector` 方法签名继续保留，但内部是数据库门面。
- `raw_panel.parquet` 只在数据库换手率为空时提供兼容值，不覆盖数据库非空字段。
- 传统特征、Transformer 特征、模型和 Streamlit 缓存路径继续兼容。

启动 Streamlit 页面：

```powershell
cd python-services\stock-analysis-app
python -m streamlit run visualization\dashboard.py
```

分析应用的模式、缓存和三条模型管线见 [代码阅读指引](python-services/stock-analysis-app/CODE_READING_GUIDE.md)。

## 页面与接口

| 地址 | 用途 |
|---|---|
| [http://localhost:8080/monitor/](http://localhost:8080/monitor/) | Java 消费监控 |
| [http://localhost:8080/actuator/health](http://localhost:8080/actuator/health) | Java 健康检查 |
| [http://localhost:8080/api/consumer/statistics](http://localhost:8080/api/consumer/statistics) | 消费统计 |
| [http://localhost:8080/api/consumer/errors](http://localhost:8080/api/consumer/errors) | 最近错误 |
| [http://localhost:8081/](http://localhost:8081/) | Kafka UI |
| [http://localhost:8501/](http://localhost:8501/) | Streamlit 分析页面 |

Java 监控数据仅保存在当前进程内存中，服务重启后重新统计；错误列表不保存完整原始 JSON payload。

## 测试与验收

Collector：

```powershell
cd python-services\python-collector
python -m pytest tests -q
```

Analysis App：

```powershell
cd python-services\stock-analysis-app
python -m pytest -q
```

Java：

```powershell
cd java-services
mvn test
```

Kafka 和 Java 启动后，在 `StockAnalysisSystem` 根目录执行冒烟测试：

```powershell
python scripts\verify_v03_smoke.py --scenario valid --count 1
python scripts\verify_v03_smoke.py --scenario all --count 20
```

## 故障与回滚

- Kafka 不可用：设置 `COLLECTOR_OUTPUT_MODE=mysql`，重启 Collector。
- daily-basic 暂不可用：日线任务可独立成功，分析端 LEFT JOIN 后新增字段为 `NULL`。
- 历史 daily-basic 未补齐：分析端仅对空值使用旧 `raw_panel.parquet` 换手率。
- 外部接口失败：任务进入 `FAILED`，使用 `retry-failed` 恢复，不能写入模拟数据冒充成功。
- 代码回滚：保留 `stock_daily_basic` 与 `stock_constituent`，它们不破坏旧分析表。

## 文档

- [V0.3 消息协议](docs/V0.3_MESSAGE_SCHEMA.md)
- [V0.3 测试用例](docs/V0.3_TEST_CASES.md)
- [V0.3 对账报告](docs/V0.3_RECONCILIATION_REPORT.md)
- [V0.3 发布说明](docs/V0.3_RELEASE_NOTES.md)
- [V0.4 发布说明](docs/V0.4_RELEASE_NOTES.md)
- [V0.4 市场数据迁移验收报告](docs/V0.4_MARKET_DATA_MIGRATION_REPORT.md)
- [Analysis App 代码阅读指引](python-services/stock-analysis-app/CODE_READING_GUIDE.md)

## 团队提交流程

1. Fork 团队仓库到个人命名空间。
2. 从团队 `main` 创建功能分支。
3. 功能分支只推送到个人 Fork。
4. 从个人 Fork 向团队仓库发起 Merge Request。
5. Merge Request 合并或确认不再需要之前，不删除关联远程分支。

## 风险提示

本项目仅用于学习和研究，不构成投资建议。股市有风险，投资需谨慎。
