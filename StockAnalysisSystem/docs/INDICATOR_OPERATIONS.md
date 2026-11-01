# 历史指标与夜间更新运维

## 三种事实不要混用

MySQL 行情已入库，不等于 ClickHouse 指标已验证；指标已验证，也不等于特征、模型或预测已更新。只有 `indicator_projection_active` 指向同一代的不可变 `CURRENT` 证明，才允许启用 V2 查询。主题偏移、进程存活、行数一致、worker 状态文件都不是验收证明。

首次引导按 `app.indicators` 的 `export → stage → seed → verify-seed → ack-covered → enable-tail → tail-once → activate` 执行。每一步都使用同一代的封存快照及已核实的 Flink 作业 ID。不能把这串命令作为无条件重试脚本，尤其不能重跑状态不明确的 `seed`。

全部命令在 `python-services/python-collector` 目录运行。`python -m app.indicators --help` 不连接依赖，也不迁移数据库。

## 夜间持续运行

原有缺口采集 worker 每晚北京时间 20:00 检查数据库并补缺，不重复请求完整历史。指标 worker 是独立进程；不会阻塞采集调度，不训练或发布模型。

完成首次真实激活后，在本地配置：

```dotenv
FLINK_REST_URL=http://127.0.0.1:8082
INDICATOR_DEPLOYMENT_FILE=.runtime/indicator_operations/<generation>/deployment.json
```

`deployment.json` 是运维确认的 V2 部署记录，包含 generation、job_id、partitions、manifest_sha256、jar_sha256、status=RUNNING。它记录部署身份，不自行授权就绪。保留实际提交的 jar、摘要、检查点引用；不能仅凭 REST 上相似的作业名称创建此记录。

启动入口为 `scripts/runtime/start_nightly_services.ps1`，从 WMI 或任务计划运行；支持 `-CheckOnly` 及显式 `-RegisterTask`。不设置 `INDICATOR_DEPLOYMENT_FILE` 时维持原有行为，不自动创建指标进程。也可单独启动：

```text
python -m app.indicators worker --deployment-file .runtime/indicator_operations/<generation>/deployment.json
```

同一数据库只允许一个指标 worker，通过连接持有的 MySQL 命名锁保护，不以本机目录锁代替跨机所有权。锁丢失、部署记录变化会停止运行；不会自动抢回锁。迁移电脑前仍必须停止原电脑的采集和分析 worker。

每轮先检查：可用内存至少 8 GiB；实际交易日历；目标日采集任务全部成功；绑定、来源及尾部投递状态。每轮最多处理 500 个事件，按股有序事务投递，ACK 和租约保持持久化。断网后由既有租约、幂等输出和后续轮询恢复；不重置任何消费偏移。ACK 后仍须完整数值核对及新鲜的已完成检查点，才能 CAS 更新 CURRENT。自动审核通过 `refresh --source-day` 固定本轮目标日，并在实际来源写锁内、审核开始及提交前复查该日采集成功；跨过 16:00 截止边界也不能擅自认证下一日。一次审核最长三小时；超时后先查数据库实际指针，不能根据控制台失败推断事务已回滚。

`deployment.json` 同目录的 `worker.state.json` 只用于诊断：

| 状态 | 含义及处理 |
| --- | --- |
| IDLE / VERIFIED | 截止日无需推进 / 本轮真实审核已通过；仍以数据库证明为准 |
| WAITING_SOURCE | 目标日尚未全部采集成功，检查原采集任务，不伪造停牌价格 |
| WAITING_MEMORY | 内存不足，释放后自动重查 |
| WAITING_DELIVERY | 仍有未确认或未到期租约，后续继续 |
| WAITING_DEPENDENCIES | Kafka、ClickHouse、检查点或完整审核未通过；检查依赖，指针不前移 |
| REPLACEMENT_REQUIRED | 新股票、历史更正/删除或修复标记，需要隔离重建，禁止清除标记后冒充修复 |
| BINDING_CHANGED | 已切换代或作业绑定，核实新部署后重新启动，不沿用旧 job_id |

## 新股、历史更正和作业恢复

普通追加复用当前代；新股或历史更正采用**受控完整替代代**，不是自动覆盖旧状态。创建新 UUID、快照、独立主题及 V2 作业；确保作业槽位、内存和磁盘足够，完成全量引导及同等审核后，以旧代/epoch 作为 CAS 预期切换。更新部署记录，再重启指标 worker。旧代、主题、检查点及模型均保留；停牌无交易行不补造。

Flink 作业发生重启或任务尝试改变，当前检查点读者会保守拒绝审核。只能从已保留且核实的检查点恢复到**新的作业 ID**，更新对应部署记录，再重新审核；不能空状态从尾部接续，也不能删除旧证据解除拦截。

已证明截止日前出现新的来源更正时，受影响股票返回 `SOURCE_PENDING`，指标查询暂不可用。精确种子覆盖 ACK 或不超出该 CURRENT 证明分区偏移的 ACK 才视为已覆盖；不是按最大事件 ID 猜测。证明截止日之后的新数据不会把旧的历史快照冒称“最新”。

## 依赖故障与回滚

专用 V2 ClickHouse 入库进程可设置 `CLICKHOUSE_TIMEOUT_SECONDS=30`；普通查询默认 5 秒，允许范围 1–120 秒。写入超时可能已经提交；保留 Kafka ACK 边界，恢复原消费组，由 ReplacingMergeTree 的版本和 FINAL 去重，不重发历史种子。健康接口会在任一启用的消费子容器停止时返回 DOWN。

初次启用前，先在只读探测实例检查 `/api/analysis/indicators/<code>/readiness` 和指标数据。正式查询入口再显式设置 `MARKET_GENERATION_READS_ENABLED=true`。需要恢复旧查询时关闭该开关并重启对应服务；这只切换读取，不清除新代、指针或证明，旧查询也必须显示 UNVERIFIED。已有上一代时，逻辑回滚仍须 `rollback` 的全审核和 CAS，不能直接修改指针表。

## 备份及跨机恢复

源表备份只能证明源表可恢复，不等于完整系统备份。完整交接还需全部业务/账户/任务/指标元数据、来源证据和日历、封存模型及信号、特征清单、快照、部署记录、Flink 检查点与 ClickHouse 数据。先停止所有写入者，再生成清单及 SHA256，恢复到隔离环境并检验后才替换正式库。账户凭据和本地配置通过私下安全渠道交接，不能提交 GitHub。

运行目录和配置路径相对 `StockAnalysisSystem` 解析，不依赖开发者盘符。新电脑仅下载代码不会得到历史模型、缓存或数据库；缺少这些数据时必须明确显示未就绪，不能用演示数据冒充真实结果。
