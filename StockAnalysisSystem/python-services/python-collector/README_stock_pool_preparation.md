# 固定股票池与共享数据准备（后端阶段）

指数和行业使用同一后端：固定成员版本 → 查库/证据缺口 → 持久任务 → 单worker补采 → 只新增入库 → 独立核对。
本阶段交付CLI，不是前端REST功能；旧采集页面仍有演示接口。没有新增策略、自动训练、Kafka重放或ClickHouse历史重算。

## 存储和版本

stock_basic/stock_daily/stock_daily_basic是共享股票数据，多个池不重复建行情表。stock_features与analysis_result仍由分析应用维护，本流程不刷新它们。
新增stock_pool_version/member/active保存固定成员和有序股票映射，data_preparation_task保存任务，market_source_evidence索引SHA验证过的源文件。
旧stock_constituent/collection_task/collection_delivery不删除、不修改其既有接口；旧成功任务标记不能替代逐股逐交易日库存检查。

默认数据根StockAnalysisSystem/.runtime/stock_pool_preparation，可由服务配置PREPARATION_DATA_ROOT指定。里面保存日历、共享因子/停复牌/基本指标证据、任务原始分片、基线和回执。
迁服务器时同步MySQL备份与整个持久数据根。模型/scaler和其绑定版本另行迁移；不要通过Git提交.env、模型、源分片或运行账本。

目录内建沪深300000300.SH、中证1000000852.SH；行业沿用现有AkShare东方财富行业成分，按行业名称创建。行业是实际获取时的当前名单，provider_snapshot_date为null，不会把请求历史日期冒充历史行业成员日期。
创建后默认复用活动版本；只有显式--new-version才重新取成分并冻结新版本。旧模型和旧版本不被覆盖，选择日期不会改变成员数量或股票顺序。
固定成分回看历史有生存者偏差。股票更换证券代码不自动合并旧编码的历史；920号段按北交所识别，不重写旧库键。缺失/退市等尚不能解释的情况报告问题而非伪造价格。

## 安装与显式迁移

在python-services/python-collector安装requirements.txt。行情请求需要现有TUSHARE_TOKEN；只读池列表/status/inspect不初始化行情客户端，只需数据库配置和已缓存证据。
由操作人员显式检查并执行infrastructure/mysql/migrations/V0.9_001_stock_pool_preparation.sql；仅创建五张新表，不DROP/ALTER旧表。服务不调用create_tables；缺表返回MIGRATION_REQUIRED。已有同名不兼容表不得删表绕过。
先记录原表schema与备份位置；服务器常规备份覆盖整个数据库。只有登记新版本/提交任务/worker才写新元数据，价格和指标缺口导入仅INSERT，已有不同值停止、不覆盖。

下面命令均从StockAnalysisSystem/python-services/python-collector执行，均为单行。

## 复用现有沪深300来源（不再抓三年）

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py pool-import-legacy
D:/Python/python.exe scripts/prepare_stock_pool_data.py evidence-import-legacy --source-id csi300-b1-20261009
D:/Python/python.exe scripts/prepare_stock_pool_data.py pool-list
D:/Python/python.exe scripts/prepare_stock_pool_data.py inspect --pool-version csi300_5739ddc829ad3650 --start 2026-09-28 --end 2026-09-30
```

legacy导入分别完整验证原冻结池和原B1源包，登记新库元数据及共享来源副本，不修改旧模型或源文件。原源包必须仍在规范项目的.runtime/csi300_repair/20261009_0930/snapshot。
检查整个2023-10-09..2026-09-30窗口仍须报告601238.SH/2026-09-15停复牌来源冲突，不以入库成功声明该问题已解决。

## 数据准备任务

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py submit --pool-version csi300_5739ddc829ad3650 --start 2026-09-28 --end 2026-09-30
D:/Python/python.exe scripts/prepare_stock_pool_data.py worker --once
D:/Python/python.exe scripts/prepare_stock_pool_data.py worker
```

submit只入持久队列，不在提交请求中采集。worker --once处理一个可运行任务；无任务输出IDLE。worker默认常驻，实际部署用服务管理器守护，网页关闭不影响任务。
status/retry的--task-id使用submit输出的实际task_id，不是手写股票代码或文档演示ID。格式为`D:/Python/python.exe scripts/prepare_stock_pool_data.py status --task-id TASK_ID_FROM_SUBMIT`，这里TASK_ID_FROM_SUBMIT明确是待替换的命令输出值；retry同样替换该值。

同池/范围/需求版本/服务端截止候选日的活动任务去重；执行时重新查库，所以排队期间其它池已填的数据不会再抓。结束后的新提交仍会复查，不把上次成功当永久缓存。
上海时间严格大于16:00才允许当日已完成交易日，否则最多前一实际交易日；cutoff_date是提交时冻结的候选日，window.end才是通过真实日历得到的实际截止。排队跨天不延长窗口。请求未来日期被裁剪并显示原因，没有有效范围返回PARTIAL。

## 创建指数/行业版本

```powershell
D:/Python/python.exe scripts/prepare_stock_pool_data.py pool-create --type index --code 000852.SH --as-of 2026-10-11
D:/Python/python.exe scripts/prepare_stock_pool_data.py pool-create --type industry --code 银行 --as-of 2026-10-11
```

这是显式成分获取操作，可能消耗接口额度；后续数据准备使用命令输出的pool_version。不要默认再跑中证1000三年补采，先inspect所需区间和缺口规模。
若同pool_id已有活动版本，这两个命令直接复用；管理员确实需要更新时追加--new-version。来源空表、成员矛盾、指数数量不完整或潜在截断均失败，不用全市场列表兜底。

## 进度、恢复和失败

- QUEUED待执行，RUNNING按阶段显示库存/源请求/批次进度。
- WAITING_RETRY保留分钟/小时冷却原deadline，不绕过；网络每个请求最多三次，权限/坏格式不盲重试。
- SUCCEEDED要求data_ready=true；PARTIAL表示有源问题或无有效日期，不能自动开始训练。
- FAILED保留安全error_kind，错误正文、Token、DB密码不返回。worker --once对FAILED/PARTIAL/WAITING_RETRY返回非0。
- acquisition_complete、db_import_complete、data_ready分开；model_ready仍false。合法PE/PE_TTM null不填0，也不因null反复请求。

worker独立30秒心跳、120秒租约，每个写批再次核对owner并锁任务行。开始/结束完整核对任务范围基线，中间每批只current-read目标键；所有原行含id和时间戳应保持不变。
事务提交后回执前崩溃，恢复时同值记already_equal，不伪称都是新插入；已完成来源SHA分片免SDK恢复。retry保留同一任务身份和证据，不覆盖坏包。
只有确认旧PID及进程出生身份已失效且租约到期才恢复；身份不明/跨主机返回RECOVERY_CONFIRMATION_REQUIRED。不得直接删除别人的worker/request锁。启动协调锁若因硬崩溃遗留，须先证明原进程不存在再由操作人员处理。
只支持同机单worker；不要把同数据卷同时挂到多个采集容器当作高可用方案。迁主机先停旧worker，再迁数据并按明确恢复步骤启动。
共享限频覆盖新worker、池创建和使用同canonical Requests的旧B1/forward入口。旧普通采集CLI尚未全面迁到此锁，运行新worker时不要并发启动这些旧CLI；后续前端只接新的统一任务服务。
旧数据被其它任务改变时不得覆盖恢复；需检查差异并新提交任务重新审计，不能对不兼容基线无限retry。

## 验收边界与来源

股票池/原始数据准备完成，不代表旧分析脚本已经接入新注册池，也不代表特征、模型或前端已完成联调。下一阶段分析应显式消费pool_version，不能改用“最新成分”绕过冻结映射。
测试采用注入源和隔离SQLite/MySQL临时表；真实中证1000/行业权限和大区间采集需另做明确验收，不拿mock绿色测试冒充。
Tushare字段依据：[交易日历](https://tushare.pro/document/2?doc_id=26)、[指数成分](https://tushare.pro/document/2?doc_id=96)、[股票资料](https://tushare.pro/document/2?doc_id=25)。920规则依据[北交所公告](https://www.bse.cn/important_news/200021629.html)。
checked HTTP客户端仍使用既有SDK路由；本轮不迁移HTTPS，不声明传输已加密。生产公网部署前还需鉴权、任务配额和受控网络配置；本CLI不开放远端写API。
