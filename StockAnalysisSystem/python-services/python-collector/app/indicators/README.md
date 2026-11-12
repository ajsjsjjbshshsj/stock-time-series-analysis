# 指标历史重建与切换控制

本模块提供事务捕获、可靠投递、不可变历史快照、历史播种、逐值审计及受控切换。
**尚未接入生产启动流程，不代表全历史指标重建或自动增量链路已经上线。**

## 阅读顺序

1. `capture.py`：显式安装日线 INSERT/UPDATE/DELETE 触发器。行情与待发送事件在同一事务内提交；范围为 2023 年起沪深 A 股。安装前必须备份并暂停源表写入；MySQL DDL 不是原子操作。启动应用不会自动执行迁移。
2. `outbox.py`：按事件编号逐项登记投递，不用最大编号游标跳过迟提交事件；租约包含持有者、代次、期限及 broker 回执。同股事件避免并发越序。登记默认禁用，底层 enable 不是上线验收接口。
3. `snapshot.py`：同一个可重复读事务导出有序行情与明确的已覆盖事件编号，保留 Decimal 精度并流式写入分股文件。导出失败不封存、不覆盖既有目录。SEALED 只代表文件完整性，不代表指标已计算或可上线。
4. `generation_repository.py`：将快照摘要、相对目录、独立主题及固定分区数登记为 STAGED，原子创建禁用的投递通道。不会提前确认覆盖事件，也不会改变现网读取目标。
5. `wire.py` / `seed.py`：精确 Decimal 消息与固定分区；每股历史数据和 SEED_END 在同一 Kafka 事务提交，另存种子回执。结果只是 SEEDED，不是已验收或已启用；提交结果不明确时拒绝盲目重播。
6. `audit.py`：仅保留最近 20 条真实观察，逐日核对价格、成交量、六个派生指标、预热规则和源版本。停牌日不造行情，也不按自然日补零。
7. `tail.py`：只向 VERIFIED/ACTIVE 且已启用的通道投递；一轮处理一个同股队首，按租约和 broker 回执确认。新股票没有种子时拒绝发送，须受控引导或建立替代批次。
8. `projection_verifier.py`：把完整股票/日期/数值覆盖、种子屏障、修复状态、来源分区和新鲜检查点合成证据。SEED 证明和 CURRENT 证明严格区分。
9. `lifecycle.py`：显式安装不可变证明表、分批确认快照明确覆盖的事件，再允许增量。激活和回滚都要求新鲜 CURRENT 证明与指针代次 CAS；`refresh` 为同一活动批次更新验收截止日，同时保留原回滚目标。
10. `source_guard.py` / `readers.py`：MySQL 原生只读表锁在验收期间排除所有源写入；按真实交易日历确定截止日。ClickHouse 使用 FINAL 和有界流式读取，Flink 读取当前作业自身的检查点提交指标，不拿消费组位点冒充检查点证据。`_dns.py` 限制不可取消的系统 DNS 工作，避免超时退出继续等待它。
11. `cli.py` / `__main__.py`：显式本地运维入口，复用上述模块；帮助和参数错误不初始化数据库、配置或 SDK。不会自动提交 Flink 作业、创建主题、重置位点、抓取行情或训练模型。

快照根目录由调用方配置，数据库只存相对批次引用。迁移、导出和登记均为显式操作；没有自动安装或启用入口。文件自身的摘要不等于授权，后续读取须核对登记库固定的摘要。

## 本地操作入口

在采集服务目录执行 `python -m app.indicators --help` 或 `python -m app.indicators status`。status 只显示有限条元数据；表已存在或批次为 ACTIVE 不等于此刻指标就绪。它不安装缺少的表，也不修改任务或触发计算。

默认快照目录为项目下 `.runtime/indicator_snapshots`，可用 `INDICATOR_SNAPSHOT_ROOT` 或全局参数 `--snapshot-root` 修改；相对路径相对 StockAnalysisSystem，而非当前终端。`--calendar-root` / `PREPARATION_DATA_ROOT` 指向已有封存日历缓存。全局参数放在子命令前。读取日历只验证已有文件，不请求供应商；缺少完整日历时拒绝导出。导出截止日按上海时间及 16:00 完成规则确定，不允许直接传入未来日期。

操作顺序（变量必须先由运维人员设置，每条命令单行执行）：

```powershell
python -m app.indicators migrate --backup-file "$backupFile" --backup-sha256 "$backupDigest" --confirm-backup-and-idle
python -m app.indicators export --generation "$indicatorGeneration"
python -m app.indicators stage --generation "$indicatorGeneration" --partitions 3
```

`indicatorGeneration` 必须是新建的 32 位小写十六进制 ID；已有目录不覆盖。迁移前须独立完成可恢复备份、记录其 SHA256 并暂停所有机器上的写入。命令只核对非空文件身份和显式确认，**不代替恢复演练，也无法证明远端进程已停止**。MySQL DDL 失败可能留下部分新增元数据；修复原因后兼容重入，不删除旧表或触发器。

下一步先按登记库固定的主题/分区/命名空间部署已知 V2 作业和独立 ClickHouse 摄取，再执行：

```powershell
python -m app.indicators seed --generation "$indicatorGeneration"
python -m app.indicators verify-seed --generation "$indicatorGeneration" --job-id "$indicatorJobId"
python -m app.indicators ack-covered --generation "$indicatorGeneration" --batch-size 1000
python -m app.indicators enable-tail --generation "$indicatorGeneration" --job-id "$indicatorJobId"
python -m app.indicators tail-once --generation "$indicatorGeneration"
python -m app.indicators activate --generation "$indicatorGeneration" --job-id "$indicatorJobId" --expected-epoch 0 --expected-generation none
```

`ack-covered` 一次只处理一批，须读取 complete 结果并重复到完成；`tail-once` 一次最多发送一个队首。不要把示例 epoch 0 用于已经切换过的库，应从 status 读取并固定本次预期值。`refresh` / `rollback` 也要求 `--job-id`、`--expected-epoch` 和 `--expected-generation`，不会绕过新鲜 CURRENT 审计或清除待修复记录。rollback 的 job ID 对应被恢复的目标批次，不是当前批次。

Kafka 使用 `KAFKA_BOOTSTRAP_SERVERS`；验收另需显式配置 `FLINK_REST_URL` 及现有 `CLICKHOUSE_HTTP_URL/DB/USER/PASSWORD`。源检查点指标刚注册或上一回调尚未覆盖收据时必须等待并重新验收，不能用 currentOffset 或消费组位点替换。任何操作失败均以非零码及净化错误返回，不泄露原始依赖异常中的密码。失败不代表所有先前步骤已回滚，检查持久状态后处理，尤其不可盲目重播 SEEDING/SEED_FAILED。

## 安全边界与运维前提

- 阶段顺序为 STAGED → SEEDING → SEEDED → VERIFIED → ACTIVE。VERIFIED 允许受控追平，不代表已经达到最新交易日。
- 源写保护必须覆盖最终元数据提交。存在待发送、租约未完成、遗漏事件或修复记录时拒绝切换；不能为了消除报错清空位点、修复证据或旧检查点。
- CURRENT 截止日表示源与投影在该上界内核对过，不等于所有股票每天都有报价，也不等于上游市场数据缺口已全部消除。原始数据、指标、模型特征和预测仍要分开判断就绪状态。
- 历史更正和删除永久标为待修复，不能静默覆盖旧指标；当前保守修复边界是建立并完整验收替代批次。
- 原生读取器须由受控同步运维线程调用，不在异步网页请求中执行全量审计；使用 `with ClickHouseProjectionReader(...)` 关闭连接池。请求头、响应碎片和 EOF 均有总时限，单行最多 1 MiB。
- 必须确认部署的是已固定的 GenerationIndicatorJob 产物和作业 ID。仅凭 REST 作业名称不能认证二进制。当前 Flink 适配器拒绝复用同一作业自动重启后的可能陈旧指标，须从保留检查点受控恢复成新的作业 ID，再取新证据；不允许用空状态代替恢复。
- 新依赖在采集服务 `requirements.txt` 声明；本机 Python 已安装不代表同事机器也已安装，迁移时仍须完整安装依赖。

## 尚需接通

- 备份后的正式迁移及真实全历史回放；本地命令不等于已经执行上线。
- 全规模故障验收和受控恢复；小规模原生检查点/回放通过不代表生产历史覆盖通过。
- 历史更正/删除后自动建立替代批次的调度，以及新股票引导。
- API 和前端读取桥已实现，默认新批次读取关闭；须在正式验收后启用。夜间调度、替代批次和新股票引导仍需接通。

上述验收完成前，不应启用新通道或清除旧主题、消费位点、检查点、模型及指标表。
