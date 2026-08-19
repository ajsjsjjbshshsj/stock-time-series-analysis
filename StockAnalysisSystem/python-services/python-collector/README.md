# 数据采集服务 (Python Collector Service)

## V0.4 数据职责

本服务是项目中唯一允许访问 Tushare/AkShare SDK 的模块。分析应用不得联网抓取行情，只读取本服务写入的 MySQL/Kafka 数据。

新增原始表：

- `stock_daily_basic`：换手率、PE/PB/PS、总市值。
- `stock_constituent`：指数/行业成分股日期快照。

## 概述

V0.2 独立数据采集服务，将原项目中的股票数据采集功能从主项目中拆分出来，
形成一个可以独立运行、独立维护、独立测试的数据采集服务。

## 核心能力

- ✅ Tushare / AkShare 统一采集接口
- ✅ 按交易日采集全市场行情
- ✅ 指定日期范围历史数据回补
- ✅ 失败任务自动重试
- ✅ 采集任务状态跟踪
- ✅ 数据幂等写入（不产生重复行情）
- ✅ 独立运行，不依赖 Dashboard 或模型训练

## 项目结构

```text
python-services/python-collector/
├── app/
│   ├── main.py                  # CLI 入口
│   ├── config.py                # 服务配置
│   ├── collectors/
│   │   ├── base_collector.py    # 采集器基类
│   │   ├── tushare_collector.py # Tushare 实现
│   │   └── akshare_collector.py # AkShare 实现
│   ├── jobs/
│   │   ├── daily_collection_job.py  # 单日采集
│   │   ├── daily_basic_collection_job.py # 单日估值指标
│   │   ├── daily_basic_backfill_job.py   # 指标历史补采
│   │   ├── constituent_collection_job.py # 成分股快照
│   │   ├── history_backfill_job.py  # 历史回补
│   │   └── retry_failed_job.py      # 失败重试
│   ├── repositories/
│   │   ├── stock_repository.py  # 行情数据读写
│   │   └── task_repository.py   # 任务状态读写
│   ├── models/
│   │   ├── stock_daily.py       # 日线数据模型
│   │   └── collection_task.py   # 任务模型
│   └── utils/
│       ├── logger.py            # 统一日志
│       ├── retry.py             # 重试工具
│       └── date_utils.py        # 日期工具
├── tests/                       # 单元测试
├── requirements.txt
├── README.md
└── Dockerfile
```

## 快速开始

### 1. 环境配置

确保项目根目录 `.env` 文件已配置：

```env
TUSHARE_TOKEN=your_token_here
DB_HOST=localhost
DB_PORT=3306
DB_NAME=stock_analysis
DB_USER=root
DB_PASSWORD=your_password
```

### 2. 安装依赖

```bash
# 方式一：复用主项目虚拟环境
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows

# 方式二：独立安装
cd python-services/python-collector
pip install -r requirements.txt
```

### 3. 运行命令

```bash
# 采集指定交易日数据
python app/main.py daily --date 2026-07-03

# 使用 AkShare 数据源
python app/main.py --source akshare daily --date 2026-07-03

# 历史数据回补
python app/main.py history --start 2026-01-01 --end 2026-07-03

# 更新股票基础信息
python app/main.py basic

# 重试失败任务
python app/main.py retry-failed

# OHLCV 与 daily-basic 使用独立事务采集
python -m app.main daily-market --date 20260817

# 只处理 stock_daily 已存在的交易日，并逐日提交检查点
python -m app.main daily-basic-history --start 20220104 --end 20260817

# 保存指数或行业成分股快照
python -m app.main constituent --type index --code 000300.SH --date 20260817
```

## 命令说明

| 命令 | 说明 | 示例 |
|------|------|------|
| `daily` | 采集指定交易日数据 | `python app/main.py daily --date 20260703` |
| `history` | 回补历史数据 | `python app/main.py history --start 20260101 --end 20260703` |
| `basic` | 更新股票基础信息 | `python app/main.py basic` |
| `retry-failed` | 重试失败任务 | `python app/main.py retry-failed` |
| `daily-basic` | 采集单日换手率和估值 | `python -m app.main daily-basic --date 20260817` |
| `daily-basic-history` | 按行情表已有日期补采 | `python -m app.main daily-basic-history --start 20220104 --end 20260817` |
| `constituent` | 保存指数/行业快照 | `python -m app.main constituent --type index --code 000300.SH --date 20260817` |
| `daily-market` | 分事务执行 OHLCV 和 daily-basic | `python -m app.main daily-market --date 20260817` |

### 全局参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--source` | 数据源 (tushare/akshare) | COLLECTOR_SOURCE 环境变量 |

## 采集任务状态

采集任务状态记录在 `collection_task` 表中：

| 状态 | 说明 |
|------|------|
| `PENDING` | 任务已创建，等待执行 |
| `RUNNING` | 任务正在执行 |
| `SUCCESS` | 任务执行成功 |
| `FAILED` | 任务执行失败 |

状态流转：
```
PENDING → RUNNING → SUCCESS
                  → FAILED → (retry) → RUNNING → SUCCESS / FAILED
```

## 数据幂等设计

- **行情数据**：`ts_code + trade_date` 唯一约束，INSERT IGNORE 去重
- **任务记录**：`task_type + business_date + source` 唯一约束
- **每日指标**：`ts_code + trade_date` 唯一约束，重复补采执行 UPSERT
- **成分股快照**：`group_type + group_code + ts_code + as_of_date` 唯一约束

重复执行同一天的采集不会产生重复行情记录。

## 环境变量

| 变量 | 必填 | 说明 | 默认值 |
|------|------|------|--------|
| `TUSHARE_TOKEN` | ✅ | Tushare API Token | - |
| `DB_PASSWORD` | ✅ | 数据库密码 | - |
| `DB_HOST` | ❌ | 数据库主机 | localhost |
| `DB_PORT` | ❌ | 数据库端口 | 3306 |
| `DB_NAME` | ❌ | 数据库名 | stock_analysis |
| `DB_USER` | ❌ | 数据库用户 | root |
| `COLLECTOR_SOURCE` | ❌ | 默认数据源 | tushare |
| `LOG_LEVEL` | ❌ | 日志级别 | INFO |

## 测试

```bash
cd python-services/python-collector
python -m pytest tests/ -v
```

## 与主项目的关系

- V0.2 期间，旧 `data_loader/` 采集入口保留，新采集服务并行运行
- 新旧入口使用相同交易日数据进行对账验证
- 新采集服务稳定后，旧入口标记为废弃

## 版本

- V0.2.0 — 采集服务独立化
- V0.3 — 采集链路 Kafka 消息化
- V0.4 — 统一 Tushare/AkShare 数据入口、daily-basic 与成分股快照
