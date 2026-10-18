# 后台训练任务与独立 worker

## 当前可用范围

管理员登录后打开 `/#/training`：创建、分页查看、跟踪和取消真实持久化任务。普通用户不能访问该页面或任务 API；`AUTH_MODE=local` 同样不提供任务管理。

支持两种 **CPU 工程候选**：单股 XGBoost（最多100棵树、深度6、单线程、seed42、一天分类标签），以及沪深 A 股全市场 LightGBM（五个市场交易日收益回归后排序，保留三任务探针）。Transformer 队列仍未接入。现有三个策略的旧入口、默认配置及旧模型不被替换。

`SUCCEEDED` 表示完成训练、候选保存及原生模型重加载验证，**不表示已发布、策略效果合格或可交易**。浏览器和此 worker 都不会发布模型。当前仅为 `engineering_smoke` 用途，不应作为正式策略使用。

## 先决条件与配置

- 按 [REMOTE_ACCESS.md](REMOTE_ACCESS.md) 配好 protected 登录、现有 MySQL 连接，并创建管理员。
- 数据需已完成 V2 特征构建和显式发布；任务只读验证来源，不自动采集/修复/重建特征。
- 提供已采集并核对过的 SSE 交易日历 JSON，包含完整的自然日覆盖；不能用工作日推测交易日。服务器配置文件路径，不由浏览器提交。

在现有 `.env` 添加以下可选配置。路径均相对 `StockAnalysisSystem`，或使用部署者自己的绝对路径；示例无需修改代码，真实文件不入 Git：

```dotenv
TRAINING_CALENDAR_FILE=.runtime/training_tasks/calendar.json
TRAINING_ARTIFACT_ROOT=.runtime/training_tasks/candidates
```

支持项目既有日历的两种形态：`source="tushare.trade_cal"`、`exchange="SSE"`、`start/end`（ISO日期），加上完整 `rows=[{"cal_date":"YYYY-MM-DD","is_open":0或1},...]`，或有序不重复 `open_dates=[...]`。日历必须覆盖 V2 来源起始日至请求截止日的完整依赖前缀，不是只覆盖表单起始日；必须来自可信采集证据，不要以工作日补造。

现有 Collector 只有 `source/rows` 的分片缓存不能直接当作完整分析日历：需先通过已有数据准备流程整理出含明确交易所与范围的分析用日历，保留其证据。没有合格日历时任务服务明确未就绪，不在线补采、不猜测。

任务创建时绑定当时的构建 ID、源/算法/特征列/运行代码/日历摘要与单股映射。worker 读取时重新核对；活跃构建已改变且不能精确读取原版时任务失败 `SOURCE_CHANGED`，不改用最新来源。开始日期只限制训练输入，V2 来源验证保留完整指标依赖历史。

## 显式初始化与启动

在 `python-services/stock-analysis-app` 目录，使用安装了本项目依赖的 Python。以下命令均为单行：

```powershell
python scripts/training_worker.py migrate
```

只创建三张 `analysis_training_*` 表及独立执行槽，不改行情/认证/模型表、不创建任务或启动 worker。等价 DDL 在 `infrastructure/mysql/migrations/V0.9_005_training_tasks.sql`。模块导入、HTTP 和 worker 启动都不自动迁移。

已有旧任务表的部署，先停止 API 与独立 worker，确认执行槽无在途任务，再显式执行（新环境也可用此幂等命令）：

```powershell
python scripts/training_worker.py migrate-all-market
```

对应 `V0.9_006_training_all_market.sql`：将绑定扩为 MySQL LONGTEXT，新增 nullable 进度/事件详情，不删除旧任务。存在占用槽时拒绝升级。DDL 会隐式提交，重复升级应使用上述幂等 CLI，不要重复裸执行一次性 SQL。迁移后再启动新 API/worker；旧 schema 不会被 HTTP 或 worker 偷偷升级。

更新代码后需要配套重启 FastAPI 和前端，旧进程不会自动加载新路由。不要因端口已 UP 就认定运行的是新版本；先正常停止旧进程，不按 python/java 进程名批量终止。

前台运行独立 worker（适合直接操作的终端）：

```powershell
python scripts/training_worker.py run
```

最多尝试领取一个任务，用于运维验收：

```powershell
python scripts/training_worker.py run --once
```

检查状态，不开始训练：

```powershell
python scripts/training_worker.py status
```

`run --once` 只是一次领取/处理操作；是否训练成功必须查看任务最终状态和 verified 候选摘要，不能仅以命令退出码判断。无 worker 时任务保留 QUEUED；关闭浏览器不影响 worker。

### Windows 持久运行

如果从 Codex 或受 Job Object 管理的工具会话拉起常驻 worker，必须使用 WMI，不能让 worker 随该会话结束被回收。先在应用目录确认所用 Python 环境，然后执行以下**单行**命令：

```powershell
$workerPython=(Get-Command python).Source; $workerFile=(Resolve-Path scripts/training_worker.py).Path; Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=('"{0}" "{1}" run' -f $workerPython,$workerFile);CurrentDirectory=(Get-Location).Path}
```

返回值0表示进程已创建，不等于任务成功；通过后台状态/心跳验收。worker 不默认接入一键启动脚本，以免重启应用时意外开始训练。Linux 可由部署者以独立 systemd 服务运行同一入口；只运行一套正式训练队列，并验证实际部署环境。

## 状态、取消与中断

| 状态 | 含义 |
|---|---|
| QUEUED | 已持久化，尚未领取 |
| RUNNING | worker 持有执行槽，阶段/心跳来自真实执行 |
| CANCEL_REQUESTED | 请求取消，仍在等待进程退出确认 |
| CANCELLED | 排队任务已撤销，或执行进程树已确认停止 |
| SUCCEEDED | 工程候选保存且重加载通过，未发布 |
| FAILED | 明确失败，保留受控候选证据；不自动重试 |
| INTERRUPTED | 进程/租约中断；不自动重训 |

默认5秒心跳、30秒租约、15秒子进程控制超时。XGBoost 最长1800秒，全市场 LightGBM 最长3600秒。两台机器/多个 worker 竞争时数据库执行槽只允许一项训练；不能因租约超时就假定 GPU/CPU 已空闲。

取消只作用于 worker 自己持有句柄的进程树。Windows 使用 kill-on-close Job Object；Linux 使用隔离进程组及父控制管道。无法证明旧进程退出时执行槽保持隔离，新任务不会开始。Windows 路径守卫拒绝符号链接与目录 junction；候选根不得与旧模型目录重叠。

### 隔离执行槽的恢复

1. 在**原 worker 机器**确认旧 worker 及其全部训练子孙进程已经退出。不要只看 Web 页面或租约时间，也不要误杀其他训练。
2. 用 `status` 检查被隔离的 task_id/worker_id/generation。
3. 在受控运维终端执行：

```powershell
python scripts/training_worker.py recover
```

该操作要求输入包含上述三个身份的完整确认短语，再隐藏输入当前管理员密码。它是管理员对已核查进程退出的显式确认，**不是跨机器自动证明**；无法确认原进程状态就不要解锁。普通用户、停用或待改密管理员不能恢复。恢复只释放匹配的隔离槽，不重排中断任务，不覆盖候选。

## 日志与候选

API 仅返回固定阶段事件和净化失败码，不回传原始 stdout/stderr、连接异常、环境变量或命令行。事件分页、总量有上限，页面切任务/隐藏/离开会停止或取消相应轮询，旧响应不会混入新任务。

候选位于配置根下的唯一 task_id 目录。XGBoost 精确包含 `model.json / metadata.json / probe.json / manifest.json`；LightGBM 精确包含 `model.txt / metadata.json / probe.json / coverage.json / manifest.json`，不能混用。父进程独立重加载并检查任务绑定、特征顺序、文件哈希和最多16行预测探针。已有目录拒绝覆盖；工程候选不写入用户预测发布表，没有任意文件下载/执行接口。

常见失败：`SOURCE_CHANGED`（创建后数据/日历/代码版本变化）、`INPUT_INVALID`（输入、日历或成熟标签不足）、`PROTOCOL_INVALID`（执行事件协议异常）、`PROCESS_FAILED`（进程异常/超时）、`CANDIDATE_INVALID`（工件不完整或重加载失败）。修复原因后显式新建任务，不静默复用原任务重跑。

## 全市场 LightGBM 的边界

- `lightgbm_all_market / all_shsz_a / bounded_v1` 是服务端固定契约，不接收浏览器股票名单、路径、SQL或任意超参数。冻结 READY V2 构建内的完整沪深 A 股名单及映射，必须与主数据一致，不含北交所。数量取真实构建，不硬编码5371。公开响应只回池数量/摘要，子进程通过当前租约及任务哈希领取完整绑定，首包不携带大名单。
- 这是**当前已入库股票池**，并不证明历年退市股票完整或没有幸存者偏差。主数据没有退市历史证明时，不猜测未知代码有效期，也不把旧代码行情关联到新代码。
- 逐股读取完整来源历史并重算核对现有62列特征，只有请求区间的合格行参与训练。上市前、自然预热、已验证全日停牌、尾部标签未成熟及目标日停牌分别报告；无合格样本股票仍保留在池。异常成熟 NaN/Inf、上市前真实报价、未解释缺口、源/参考/证据变化都失败，不能通过 dropna 缩池。停牌证明复用 collector 已封存的来源文件、索引与哈希校验；不会在线补采。
- 标签是**确切第5个 SSE 市场交易日**收盘价相对信号日收盘价的收益，不是停牌后的第5笔行情。按所有合格样本的唯一信号日期60/20/20切分，训练标签兑现日严格早于验证首日，验证标签兑现日严格早于测试首日。预热保留完整历史，不仅从训练开始日计算指标。
- `coverage.json` 保存每股资格原因/日期范围（包括零样本股）、每日有效股票数和净化排除数量；它不改变冻结股票 ID。矩阵为独立磁盘 float32 输入，标签 float64；不改原数据库 double 和历史缓存。
- 探针仅在外层训练分区内部再次按整日90/10切分并净化；1次迭代、10噪声列、分类/回归/方向各最多30轮。沿用实际旧规则：任一任务重要性**超过**其噪声最大值即可保留，三者均不超过才删除。筛选为空即失败，不回退全特征。最终收益回归最多20轮，CPU2线程、seed42；工程 RMSE/方向准确率不是策略收益或上线认证。
- 启动需空闲内存至少8 GiB、工件盘至少16 GiB；每秒核查自有进程树 RSS 上限6 GiB、磁盘安全余量2 GiB，时限60分钟。Dataset 按磁盘 Sequence 分批构造（最多4096行/批），噪声也存磁盘，不抽股/抽日期。`RESOURCE_UNAVAILABLE / RESOURCE_LIMIT / TIME_LIMIT` 明确失败，不降级成小股票池。
- 阶段为预检→输入→探针→训练→重加载→保存；进度是服务端确认的计数，不是估算百分比。元数据记录实际参数、源/池身份、切分、探针、资源峰值与耗时；公开候选摘要至多8 KiB，metadata/probe至多2 MiB、coverage至多16 MiB。
- 临时矩阵保留在候选根同级的 `work/<task_id>`。输入拒绝时其中 `input_failure.json` 仅含股票、日期和固定原因码；不含 SQL/密码/绝对源路径。诊断和缓存不提交 Git。确认无在途任务及不再需要审计后，运维者可针对该明确任务目录归档/清理，不要删除旧封存模型。

## 验收边界与回退

隔离测试以合成行情实际运行小 XGBoost/LightGBM、V2 来源核验及真实 MySQL/子进程；浏览器测试使用合成源元数据与呈现夹具验证队列交互，不宣称正式市场模型效果通过。正式全市场任务必须另行在正常管理员会话中提交，最终以绑定完整池、覆盖、资源和候选核验结果为准；失败不换池重提。

部署本身不自动开始训练、采集或发布，也不变更公网入口。更新代码前必须先确认空闲并停止旧 worker/API，不在已绑定任务执行中换代码。回退时停止入口和 worker、处理在途任务，再恢复旧代码；新增 nullable 列和更大绑定保留即可，不通过删除任务/行情/模型恢复运行。
