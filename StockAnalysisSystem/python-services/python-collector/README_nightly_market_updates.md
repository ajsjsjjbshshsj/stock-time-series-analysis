# 沪深 A 股夜间更新

目标数据范围为沪深 A 股、2023-01-01 起，不包含北交所、B 股、指数或基金。
默认先扫描日线、基本指标主键，只补缺失股票日期；必要时复用或查询目标停牌证据。不会自动训练模型、计算特征或全量补复权因子。
数据库存储全市场行情，用户选择股票池时复用其中对应股票的数据。

## 证券代码与日期边界

每个代码独立存储，不关联旧新代码、不复制价格、不拼接历史。
`300114.SZ` 只属于 2025-02-17 之前；`302132.SZ` 从该日开始。
新股从真实上市日起纳入，实际退市日及之后不纳入，不补造上市前数据。
旧代码缺失的历史资料来自[深交所上市记录](https://www.szse.cn/aboutus/sse/events/t20110119_497844.html)，
代码边界来自[2025-028 公告](https://disc.static.szse.cn/download/disc/disk03/finalpage/2025-02-15/cedb693a-f5ee-4463-9682-ea33d406b569.PDF)。
历史资料中未知行业、地区保持为空；历史上市状态不代表当前供应商状态，原始 Tushare 参考包不修改。

新版入口使用 `market-gap-v1:批次哈希前16位`/名单格式 2。同日任务还绑定完整覆盖快照和批次哈希，不复用另一覆盖快照的成功状态。
旧 `market-v1`、`market-v2` 任务和已绑定名单保留、可按 ID 查询，不由新 worker 执行；
对旧任务执行新版 `nightly-retry` 会明确拒绝，需重新提交日期生成新版任务。
只有已核验参考资料证明代码不在有效期内的复权因子行可以排除，并保存来源哈希与排除审计。
供应商可能同时返回新代码生效前的历史价格；只有明确核准的代码有效期外记录会审计排除，不改名复制到其它代码。
其它未知股票、名单外行情或基本指标仍报错；原始响应始终保留。

## 旧数据精度兼容

旧审计策略 `market-v2` 在已核验来源上采用显式精度兼容：只有整条数值记录能严格重现
“来源值转 IEEE binary32，再按实际 MySQL DECIMAL 精度存储”的结果，才接受旧值。
不设置宽泛误差范围，不更新旧行情，不把量化后的值用于新增行。
缺少完整 DECIMAL 类型声明、空值不一致、或无法重现的差异仍失败。
旧冻结修复包及默认比较保持严格规则，序列化报告格式不变。

结果中的 `legacy_float32_compatible_count` 是兼容旧值的记录数；每次运行封存
`market_updates/TASK_ID/attempts/ATTEMPT/precision_compatibility.json` 及其哈希证明，
绑定原始 SQL 基线、已核验来源清单、差异字段和比较哈希。
旧 float32 已经丢失的信息无法恢复，同一 binary32 量化区间内的更小变化无法区分；
可分辨且不满足上述严格重现条件的变化仍被拦截。

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
优先读取 `python-collector/.env`，不存在时读取 `StockAnalysisSystem/.env`；进程环境优先于文件，显式命令参数优先于两者。夜间配置在初始化数据库和 SDK 前加载并校验。
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

同一批次同一天的任务身份固定。显式重新提交日期会重新扫描数据库；覆盖改变会产生新绑定，不把旧成功当作新检查结果。
明确重试 FAILED/PARTIAL 会增加 attempt，保留原名单、缺口清单及来源证明。
额度/忙锁等待保留未来重试时间；断点恢复复用校验过的源文件和入库回执。
已经入库但尚未保存回执的同值目标会记为 `already_present`，不是重复新增。竞争目标有差异时保留旧值并报告。
每次认领最多处理 25 个未完成的来源请求，保存分片后以无错误的 WAITING_RETRY 让出任务；不会伪装成配额错误。

控制器发生权限或证据错误后会阻塞日历刷新，不会每几秒重试接口。
先停止 worker，再执行下列命令以取得同一根目录维护锁，仅清除控制器错误。
不会重置共享配额、删除陌生进程的锁、或覆盖损坏控制文件。

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py nightly-retry --scheduler
```

## 重启补漏与历史范围

先只读统计现有数据库的缺口，再明确排队历史补采，沿用同一个入口（不需要第二个采集脚本）：

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py market-backfill --start 2023-01-01 --end 2026-10-11 --dry-run
D:/Python/python.exe scripts/prepare_stock_pool_data.py market-backfill --start 2023-01-01 --end 2026-10-11
```

dry-run 只读本地已核验资料、日历和数据库主键，缺少可信缓存时明确失败，不初始化行情 SDK或写任务。
排队后由上述 nightly worker 执行；旧暂停批次与断点不会删除或重启。
同股票连续缺失交易日合并（周末不拆分），已有中间日期与年份边界会截断区间。
只缺指标不请求日线，合法 PE/PE_TTM 空值不产生缺口。仅整个目标日期该表没有记录时允许整日请求。
冻结规划前先核验缺失键对应的可信停牌缓存，已证实无报价键不再请求日线；空响应不会直接证明停牌。未知缺口保持 PARTIAL，不填零、不前向填充。
无报价断点绑定具体停牌来源及哈希，恢复和最终发布都重验；最终只针对本批全部候选回执重新核验数值，不扩展为存量数据审计。
全市场源响应的原查询覆盖与可写目标分别绑定，后续新报价指标从同一可信原响应提取，不重复下载。
指标 followup 按父来源只冻结一次，按日期索引读取相关分片；单键规划不遍历全市场股票×日期矩阵。
原文件、原参数、哈希证明完整的来源才能复用，旧 SQL 合并输出不作为原始行情缓存。
只插入冻结缺口，新增候选和回执恢复只核查目标键，不重新逐字段核对全部旧行情。

结果 `verification_mode=missing-only`、`existing_data_revalidated=false`；`data_ready` 仅表示核心行情/指标覆盖，`model_ready=false`。
计数包括已跳过键、计划缺口、合并分片、实际请求/缓存、新增行、已证实无报价和未解决键。

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
- 真实接口还包含 2006 年退市旧实体 `T600018.SH`：只在该历史身份、交易所和上市/退市日期精确一致时排除，并保留原始记录及排除审计。绝不把它改成当前 `600018.SH`；其它未知别名仍报错。
- 明确盘中停牌时段、同时有供应商校验日线的 S/R 组合，不当作全天冲突；没有有效日线、时间未知或覆盖整场的组合仍不猜测。旧冻结修复包保持原有规则重放，避免改写其既定报告。
- `source_complete`、`db_import_complete` 与 `data_ready` 分开报告；PARTIAL 不是验收成功，数据完整也不表示模型已经训练。
- 已有行情行、schema、旧源证据、清理备份及模型文件不删除或覆盖；只有缺失且验证通过的新行插入。
- Ctrl+C 可停止，重启后核对真实进程身份再恢复；跨主机/未知 owner 不自动抢占。
- 本阶段没有安装 Windows 计划任务或系统自启动。关机时无法采集，服务器上线需由容器/服务管理器维持进程。
- 现有 Tushare 检查层复用自有 HTTP 会话并保留超时、错误分类和限频；现有公网 HTTP 端点不等于加密传输。本阶段不宣称完成 TLS、鉴权或高可用部署。
