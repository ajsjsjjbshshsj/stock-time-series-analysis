# 统一市场数据采集入口设计

## 1. 背景

项目目前存在两套外部市场数据访问路径：

- `python-collector/app/collectors/` 中的 `TushareCollector` 和 `AkShareCollector`，负责 V0.3 的 MySQL/Kafka 采集链路；
- `stock-analysis-app/data_loader/` 中的 `TushareAPI`、`AkshareAPI` 和 `DataCollector`，仍被命令行分析、面板构建、股票筛选和 Streamlit 页面直接调用。

两套实现重复初始化客户端、转换证券代码、获取日线和股票基础信息。更重要的是，两边的数据能力并不完全相同：旧 Tushare 路径会合并 `daily_basic`，而新 Collector 的全市场日线目前只持久化 OHLCV，缺少换手率、估值和市值数据。

本次改造统一职责边界，同时保留旧分析功能的输入字段和缓存行为。

## 2. 目标

- `python-collector` 是唯一允许直接访问 Tushare 和 AkShare 的服务。
- `stock-analysis-app` 只从 MySQL 等内部存储读取数据，不再访问外部行情 API。
- Tushare 和 AkShare 在 Collector 内通过统一接口提供行情、基础信息和成分股数据。
- 新链路覆盖旧链路已有的行情、换手率、估值、市值、指数成分股和行业成分股能力。
- 保留旧分析命令、面板构建、模型训练、Parquet 增量缓存和 Streamlit 页面缓存。
- 对历史 `daily_basic` 数据执行可恢复、可重复的回补。

## 3. 非目标

- 本轮不实现 Flink 指标计算。
- 本轮不引入 ClickHouse 或 Redis。
- 本轮不把 `daily_basic` 字段直接写入 `stock_features`。
- 本轮不改变现有 `StockDailyEvent` Kafka 契约。
- 本轮不删除 Parquet、模型或 Streamlit 缓存。

## 4. 核心职责边界

```text
Tushare / AkShare
        ↓
python-collector
  ├── 外部 API 调用
  ├── 字段标准化
  ├── 任务、重试和限流
  ├── MySQL 持久化
  └── Kafka 行情事件
        ↓
MySQL / Kafka
        ↓
stock-analysis-app
  ├── 数据库查询与组合
  ├── 特征计算
  ├── Parquet 增量缓存
  ├── 模型训练和预测
  └── Streamlit 展示缓存
```

`stock-analysis-app` 不导入 `python-collector` 的内部模块，也不共享外部 API 客户端。两个服务只通过持久化数据契约衔接。

## 5. Collector 内部结构

外部 API 调用从 Collector 适配器中拆分为轻量 Provider：

```text
python-collector/app/market_data/
├── codes.py
├── tushare_client.py
└── akshare_client.py

python-collector/app/collectors/
├── tushare_collector.py
└── akshare_collector.py
```

Provider 只负责调用第三方 SDK，不读取 `.env`、不连接数据库、不管理任务。Token、客户端实例和配置通过构造函数传入。

Collector 继续实现 `BaseCollector`，负责：

- 把第三方字段转换为项目统一字段；
- 把日期转换为统一类型；
- 处理空结果和可降级字段；
- 提供统一的数据源名称；
- 将异常交给 Job 层的重试策略。

统一代码转换只保留在 `market_data/codes.py`，消除 Tushare、旧 API 和 AkShare Collector 中的重复实现。

## 6. 统一采集能力

Collector 对外提供以下能力：

```python
collect_daily(trade_date: str) -> pd.DataFrame
collect_daily_single(ts_code: str, start_date: str, end_date: str) -> pd.DataFrame
collect_daily_basic(trade_date: str) -> pd.DataFrame
collect_basic() -> pd.DataFrame
collect_trade_calendar(start_date: str, end_date: str) -> pd.DataFrame
collect_index_constituents(index_code: str, as_of_date: str) -> pd.DataFrame
collect_industry_constituents(industry_code: str, as_of_date: str) -> pd.DataFrame
```

Tushare `daily_basic` 标准字段为：

```text
ts_code, trade_date, turnover_rate, pe, pe_ttm, pb, ps, total_mv
```

AkShare 日线若返回换手率，则映射为 `turnover_rate`；无法提供的 PE、PB、PS 和市值字段保持 `NULL`，不得伪造或从价格反推。

## 7. 数据模型

### 7.1 stock_daily_basic

该表保存数据源直接提供的每日换手率、估值和市值，是原始明细数据，不是计算特征。

```text
id              BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT
ts_code         VARCHAR(10) NOT NULL
trade_date      DATE NOT NULL
turnover_rate   DECIMAL(20,6) NULL
pe              DECIMAL(20,6) NULL
pe_ttm          DECIMAL(20,6) NULL
pb              DECIMAL(20,6) NULL
ps              DECIMAL(20,6) NULL
total_mv        DECIMAL(24,4) NULL
source          VARCHAR(20) NOT NULL
created_at      DATETIME NOT NULL
updated_at      DATETIME NOT NULL
UNIQUE(ts_code, trade_date)
INDEX(trade_date)
```

`stock_features` 继续只保存项目计算结果。例如 `stock_daily_basic.turnover_rate` 是原始输入，`stock_features.turnover_rate_5` 是最近 5 日计算结果。

### 7.2 stock_constituent

```text
id              BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT
group_type      VARCHAR(20) NOT NULL       # index / industry
group_code      VARCHAR(30) NOT NULL
ts_code         VARCHAR(10) NOT NULL
as_of_date      DATE NOT NULL
weight          DECIMAL(20,6) NULL
source          VARCHAR(20) NOT NULL
created_at      DATETIME NOT NULL
UNIQUE(group_type, group_code, ts_code, as_of_date)
INDEX(group_type, group_code, as_of_date)
```

指数权重不存在时 `weight` 为 `NULL`。行业成分股以采集日作为 `as_of_date`，从而保留历史快照。

## 8. 任务和数据流

三类数据使用独立任务类型：

```text
daily          → stock_daily + 现有 StockDailyEvent
daily_basic    → stock_daily_basic
constituent    → stock_constituent
```

每日调度可以顺序触发 `daily` 和 `daily_basic`，但两者分别记录状态。估值接口失败不得回滚已经成功的行情数据；失败任务可以独立重试。

当前分析服务依赖 MySQL，因此完整分析兼容要求 Collector 使用 `mysql` 或 `dual` 输出模式。`kafka`-only 模式继续只保证行情事件链路，不承诺分析数据库实时完整。

## 9. 分析端兼容层

新增数据库读取边界：

```text
stock-analysis-app/data_loader/market_data_repository.py
```

公开能力：

```python
fetch_single(code: str, start_date: str, end_date: str) -> pd.DataFrame
fetch_stock_list() -> pd.DataFrame
load_daily_panel(stock_codes: list[str], start_date: str, end_date: str) -> pd.DataFrame
get_index_constituents(index_code: str, as_of_date: str | None = None) -> pd.DataFrame
get_industry_constituents(industry_code: str, as_of_date: str | None = None) -> pd.DataFrame
```

行情查询连接 `stock_daily` 和 `stock_daily_basic`，返回旧分析流程需要的全部列。`trade_date` 在兼容入口保持旧分析代码使用的 Pandas datetime 类型。

原 `DataCollector` 暂时保留为兼容适配器：

- 保留类名和 `fetch_single`、`fetch_stock_list`、`collect_all_stocks` 方法；
- 内部改为调用 `MarketDataRepository`；
- `use_tushare` 参数暂时接受但不再触发联网，仅用于兼容旧调用签名并输出弃用日志；
- 数据源选择移动到 `python-collector` 配置。

所有命令行、面板构建、股票筛选和 Dashboard 完成数据库迁移后，删除 `tushare_api.py`。兼容 `DataCollector` 可在后续独立版本中删除，本轮不强制删除其公开类。

## 10. 缓存兼容

代码中未发现 `requests-cache` 或请求级磁盘缓存。现有缓存属于分析层，全部保留：

- `raw_panel.parquet`：原始面板和增量计算输入；
- `panel_features.parquet`：传统特征结果；
- `features_*.parquet`：Transformer 特征结果；
- 模型、Scaler 和股票索引映射文件；
- Streamlit `@st.cache_data(ttl=3600)` 页面缓存；
- MySQL `stock_features` 计算结果。

数据库读取结果必须继续包含 `turnover_rate`，保证已有 Parquet 增量逻辑不因数据源迁移而缺列。

## 11. 历史回补

历史 `daily_basic` 回补范围以 `stock_daily` 的最早和最晚交易日为边界。

执行策略：

1. 从 `stock_daily` 查询实际交易日列表；
2. 每个交易日建立独立 `daily_basic` 任务；
3. 按交易日调用 Tushare 全市场 `daily_basic`；
4. 使用批量 upsert 写入 `stock_daily_basic`；
5. 成功后记录行数和完成状态；
6. 失败时记录错误，后续从失败日期继续；
7. 重复执行时更新同一业务键，不产生重复记录。

回补命令必须支持起止日期、单日重试和限速配置。不得一次把全部历史数据加载到内存。

## 12. 错误处理

- 第三方限流、超时和临时网络错误使用现有指数退避策略。
- Tushare/AkShare 返回空数据时区分非交易日、数据源不支持和真实异常。
- `daily_basic` 失败只影响对应任务，不删除已有行情。
- 数据库批次失败时回滚当前批次，不把任务标为成功。
- AkShare 缺少估值字段属于正常降级，不记为伪失败。
- 分析端缺少 `stock_daily_basic` 记录时保留行情行，并把估值字段返回为 `NULL`。

## 13. 测试策略

### Collector

- Provider 使用模拟 SDK 验证调用参数和异常透传；
- Tushare/AkShare Collector 契约测试验证统一列名和日期类型；
- 代码转换工具覆盖沪、深、北交所和已有后缀；
- `daily_basic` 空值、缺列和精度测试；
- Job 测试验证行情成功、估值失败时任务状态相互独立；
- Repository 测试验证 upsert 幂等。

### Analysis

- `MarketDataRepository` 查询和 JOIN 测试；
- 兼容 `DataCollector` 的方法签名和返回列测试；
- 固定样本对比旧 API fixture 与数据库结果；
- `turnover_rate` 能继续进入 Parquet 原始面板；
- 指数、行业成分股按最新快照读取；
- 命令行和 Dashboard 不再创建 Tushare/AkShare 客户端。

### 回归

- Python Collector 当前 90 项测试必须继续通过；
- stock-analysis-app 当前 28 项测试必须继续通过，6 项环境相关测试允许保持跳过；
- 使用一个真实交易日执行 Tushare 行情与 `daily_basic` 对账；
- 使用一个 AkShare 股票样本验证字段降级；
- 历史回补至少执行“首次写入、重复执行、中断恢复”三个受控场景。

## 14. 实施顺序

1. 在 Collector 内统一 Tushare/AkShare Provider 和代码转换。
2. 新建 `stock_daily_basic`、`stock_constituent` 迁移 SQL 和 Repository。
3. 增加独立任务、每日采集和历史回补命令。
4. 创建分析端 `MarketDataRepository` 和 DB-backed `DataCollector` 兼容层。
5. 替换命令行、面板、筛选和 Dashboard 的联网调用。
6. 验证旧功能、缓存和真实数据一致性。
7. 删除 `stock-analysis-app/data_loader/tushare_api.py`。
8. 推送个人 Fork，并从个人 Fork向团队仓库创建 Merge Request。

## 15. 验收标准

- 只有 `python-collector` 能导入 Tushare/AkShare SDK。
- `stock-analysis-app` 运行分析和页面时不访问外部行情 API。
- 新数据库查询包含旧流程使用的行情、换手率、估值和市值字段。
- Tushare 与 AkShare Collector 使用一致的公开采集接口。
- 历史 `daily_basic` 回补可重复、可恢复且不产生重复记录。
- Parquet、模型和 Streamlit 缓存行为保持不变。
- 固定样本的旧 API fixture 与新数据库读取结果在声明精度内一致。
- 完整 Python 测试和真实受控验收通过。
- 所有提交只推送到个人 Fork，再向团队仓库提交 MR。
