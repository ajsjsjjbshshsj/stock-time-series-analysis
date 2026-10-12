# 沪深 A 股夜间更新

目标数据范围为沪深 A 股、2023-01-01 起，不包含北交所、B 股、指数或基金。
维护日线、基本指标，以及可复用的复权因子和停牌来源证据；不会自动训练模型或计算特征。
数据库存储全市场行情，用户选择股票池时复用其中对应股票的数据。

## 先准备数据库

先通过数据库客户端显式执行 `../../infrastructure/mysql/migrations/V0.9_002_nightly_market_updates.sql`。
它只新增 `market_update_run`，不修改原行情表、股票池表或模型文件。
需要已完成股票池阶段的元数据迁移；命令不会隐式建表。

以下命令从 `python-services/python-collector` 目录运行，每条均为单行。
默认旧 worker 不启用夜间更新，不需要新增任务表。

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py worker --nightly --nightly-at 20:00
```

该进程必须持续运行，才能在上海时区每晚 20:00 排队更新实际交易日。
非交易日不创建行情任务。可以配置 `NIGHTLY_UPDATE_AT=20:00`，显式参数优先。
`--nightly-at` 和 `--bootstrap-start` 必须与 `--nightly` 一起使用。
`COLLECTION_REQUEST_INTERVAL` 至少 0.5 秒；共享限频、配额冷却和请求锁继续生效。

## 手动排队、查询与恢复

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-submit --date 2026-09-30
D:/Python/python.exe scripts/prepare_stock_pool_data.py worker --nightly --once
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-status
```

`--once` 只处理一个可运行任务，可能是此前股票池队列中的任务。
必须比较输出 `task_id`，再查询实际提交的任务；将下例 `TASK_ID` 替换为输出的真实 ID。

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-status --task-id TASK_ID
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-retry --task-id TASK_ID
```

同一天的任务身份固定。普通提交不会重复创建 FAILED/PARTIAL/SUCCEEDED 任务。
明确重试 FAILED/PARTIAL 会增加 attempt，保留名单和最初 SQL 基线。
额度/忙锁等待保留未来重试时间；断点恢复复用校验过的源文件和入库回执。
已经入库但尚未保存回执的同值行会记为 `already_equal`，不是重复新增。

控制器发生权限或证据错误后会阻塞日历刷新，不会每几秒重试接口。
先停止 worker，再执行下列命令以取得同一根目录维护锁，仅清除控制器错误。
不会重置共享配额、删除陌生进程的锁、或覆盖损坏控制文件。

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-retry --scheduler
```

## 重启补漏与历史范围

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py worker --nightly --bootstrap-start 2026-09-28
```

没有控制文件、没有已有任务且没有指定起点时，仅从最近已到期的实际交易日开始。
之后重启会从保存的起点逐日补漏，单轮最多排队 31 个遗漏日期。
可用 `NIGHTLY_BOOTSTRAP_START` 指定起点；必须不早于 2023-01-01、不能在未来。
旧历史缺口不会因为开启普通夜间模式就自动全部采集；全历史补采需明确选择起点。
FAILED/PARTIAL 日不阻止后续日期排队，也不被当成连续完成。

## 安全边界与部署限制

- 同一数据根目录只运行一个统一 worker，公平轮转股票池和夜间更新队列，任务具有独立租约与心跳。
- 夜间模式下，旧股票池任务如果包含非沪深 A 股或请求 2023 年前数据，会失败而不是裁剪成员；固定模型映射不变。
- 不要用旧普通采集 CLI 并发回填 2022/北交所数据。默认关闭夜间模式时，旧流程仍兼容。
- 基础资料仅补缺失项，不覆盖已有名称、行业和上市日期。名单来源是采集时资料加上市/退市日期边界，不宣称是历史名称/状态快照。
- 原始源可能含其它市场，保留原始包用于审计；目标入库仍限定沪深 A 股。未知合法代码、来源冲突、无法解释的缺失不制造价格或填零。
- `source_complete`、`db_import_complete` 与 `data_ready` 分开报告；PARTIAL 不是验收成功，数据完整也不表示模型已经训练。
- 已有行情行、schema、旧源证据、清理备份及模型文件不删除或覆盖；只有缺失且验证通过的新行插入。
- Ctrl+C 可停止，重启后核对真实进程身份再恢复；跨主机/未知 owner 不自动抢占。
- 本阶段没有安装 Windows 计划任务或系统自启动。关机时无法采集，服务器上线需由容器/服务管理器维持进程。
- 现有 Tushare 检查层复用自有 HTTP 会话并保留超时、错误分类和限频；现有公网 HTTP 端点不等于加密传输。本阶段不宣称完成 TLS、鉴权或高可用部署。
