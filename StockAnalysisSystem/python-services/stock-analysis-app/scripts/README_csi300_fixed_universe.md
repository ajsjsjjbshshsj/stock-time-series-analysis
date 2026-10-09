# 沪深300固定成分池：版本冻结与数据预检

本阶段只冻结真实成分证据及检查本地数据，不补300股完整历史、不训练、不接前端或部署。
固定快照回测过去存在幸存者偏差，不是历史动态沪深300盲测。

从stock-analysis-app目录运行单行命令：

```powershell
D:/Python/python.exe scripts/run_csi300_universe.py --stage freeze
```

首次由Collector配置读取已有Token，按月最多查询当前与前两个月的index_weight；
该接口是月度数据且文档要求2000积分：https://tushare.pro/document/2?doc_id=96。
源trade_date与请求as-of日期分别保存。权限/配额失败停止留安全证据，不换源或凑股。
原始权重不归一、最新组不足300或重复则拒绝，不把多个快照取并集。
共享请求状态在StockAnalysisSystem/.runtime/market_requests中，继承已有采集的已知冷却，忽略且不提交。

成功产物在models/universes/csi300_fixed/csi300_<identity>，包含300股固定排序/索引和源SHA。
根active_pool.json绑定首次版本；以后默认freeze只验收这个版本，即使系统日期变化也不更新成分。
指定不同as-of不能悄悄替换旧版本。以后重新建池/训练是另一个明确操作，旧20股产物保留。

```powershell
D:/Python/python.exe scripts/run_csi300_universe.py --stage verify --output models/universes/csi300_fixed/csi300_<identity>
```

数据预检须指定已冻结pool、该池内单股和覆盖范围/当前时间的可信本地trade_cal JSON：

```powershell
D:/Python/python.exe scripts/run_csi300_universe.py --stage diagnose --pool models/universes/csi300_fixed/csi300_<identity> --calendar PATH_TO_TRUSTED_CALENDAR.json --stock 000001.SZ
```

只读取MySQL stock_basic与日线/daily_basic联表，不调用旧缓存补值、不DDL/落任务表。
严格北京时间大于16:00才选择当日，之前/休市用最近已完成开市日；可用--end诊断更早历史。
默认向前三年；可信日历覆盖不足时明确缩短范围，不猜休市；没有日历则完整NOT_READY、不读DB。
没有因子manifest不冒充已复权。--factor-manifest只接受明确供应商/完整标记和实际Parquet SHA证明；
原始路径文件只读，诊断另存快照以支持离线重算。

报告位于models/universes/csi300_readiness/<run>/readiness.json，verify同一目录离线重算，不联网/读DB。
保留全部300身份，逐股列上市边界、缺股/开市日、字段、观察窗口、缺因子/预热不足原因。
single_xgb只检查选中的池内成员；LightGBM/Transformer检查完整池，不能静默缩成可用20股。
profile1为保守数据检查：单股60预热+60成熟样本，LightGBM120预热+365成熟样本，
Transformer253预热+60序列+60成熟样本；不足则NOT_READY，不填假价格。
data_ready不等于模型已训练或策略盈利，model_ready在本阶段一律false。

有效mature_samples只在该合同要求的完整连续来源成立时计数；有缺口/非法字段/缺因子时
保守为0。potential_mature_samples只是按行数推导的潜在上界，不能当可训练样本。
源、池及报告都先验收staged内容再发布complete标记；离线verify不需要DB配置，不建日志或连接数据库。
共享冷却继承由Collector在同一请求锁下合并最大deadline，直接CLI同样执行，分析端不重写共享状态。
初次报告与修复后的最终报告分别保留；旧计算合同无法精确重现时不得作为最新验收证据。

阅读顺序：Collector csi300_constituent_snapshot→analysis/csi300_universe→
data_loader/csi300_readiness_source→analysis/csi300_readiness→本runner。
部分产物/锁冲突/坏SHA应保留证据排查，不自动删除或覆盖。
