# Transformer 单次采集与前向信号

单次命令串联collector采集与analysis冻结推理，六模型全部保留；不训练、不调参、不择优、
不部署、不写DB/Kafka、不创建调度、不下单。旧模型/快照/因子/缓存保持只读。

## 单行命令（stock-analysis-app目录）

```powershell
D:/Python/python.exe scripts/run_transformer_forward_signals.py --stage run --freeze-dir models/transformer/forward_freeze_20261005 --output models/transformer/forward_signals_20261008
```

```powershell
D:/Python/python.exe scripts/run_transformer_forward_signals.py --stage verify --freeze-dir models/transformer/forward_freeze_20261005 --output models/transformer/forward_signals_20261008 --signal-date 2026-10-08
```

verify的日期应改为实际已发布日期；run可显式--signal-date，但没有虚构发布时间参数。
凭证只用python-collector现有配置，不在分析侧读取或输出。trade_cal权限不足会失败，
不降级工作日猜测；daily/adj_factor串行限频，分钟/小时配额保存至少61/3601秒冷却。
网络有限重试；权限/schema不重试。成功分片可复用，失败分片与安全acquisition.json保留。
本冻结组只允许上述唯一forward_signals_20261008输出目录，不能换一个输出目录重发同日预测。
所有采集阶段/日期共享根目录request_state.json及请求锁，自动继承已有分片ledger的已知
冷却deadline，不存Token；切换日期或扩展日历也不能绕过冷却/最小请求间隔。
缓存日历未来窗口不足T+5时另存扩展快照，旧日历证据不覆盖。

## 口径与状态

SZSE完整日历核对冻结历史起点至信号T，包括休市及T+1/T+5。T16:00之后使用已完成日线，
须20股日线/因子齐备。停牌/缺股不补零、不前填、不换股票池。仅追加2026-09-30之后数据。
保持每股完整冻结prefix，沿原factor bases与量额单位、203特征、scaler和模型，不重锚/fit。
新因子文件/合并因子证据独立绑定，训练因子SHA仍指训练来源，不冒充当前整个快照。

6×20评分全部生成并完成payload原子可见后才记录publication receipt；下一交易日09:00
之前为PROSPECTIVE_CANDIDATE，等于或晚于09:00为LATE_DIAGNOSTIC。没有新完成交易日
返回NO_NEW_SESSION，不把9月30日诊断重新包装为盲测。receipt/seal缺失或哈希异常不得接受。
候选只证明输入与时效资格，不是已完成盲测收益，也不是概率或投资建议。
每天可保存候选，但未来交易仍按冻结4交易间隔非重叠规则，不能据候选收益择种子或日期。

## 阅读与失败处理

1. python-collector/app/market_data/forward_snapshot.py：分片/限频/配额/身份和SHA；collector脚本不写DB。
2. analysis/transformer_forward_contracts.py：日期、时效、逐股全历史与训练因子basis契约。
3. analysis/transformer_forward_inference.py：历史证明匹配后使用原scaler和无标签推理。
4. analysis/transformer_forward_store.py：独占日期锁、attempt、完整payload/marker、receipt和seal。
5. 本runner：验证freeze receipt与旧SHA→采集日历/新增行情→完整proof→六模型→发布→独立verify。

输出calendars保存日历证据，acquisitions按T保存新增数据和安全请求/失败ledger，attempts保留
失败过程，signals/T保存payload.json/marker.json/receipt.json/seal.json。旧文件SHA在根目录。
同一冻结组/T只有一个完成包，及时或迟到均不可覆盖；重复run只verify，不采集或推理。
锁冲突/异常残留、部分发布/无receipt请先保留全部证据并检查，程序不自动删除或修复原包。
安全错误显示白名单stage/code/retryable/evidence；失败记录写failures，不输出原始provider异常。
默认run使用真实当前时钟确定最近完成日；已及时完成包以后verify不会因当前时间晚而变迟到。

尚未实现未来收益/净值成熟评估、盲测调仓锚点/最终区间、前端/自动调度或真实成交。
成本/可分割复权资产单位仍是假设，没有整手/最低佣金/税费/停牌限价/队列/容量模拟。
本地校验防止不一致/意外改写，不抵御同时篡改全部文件、receipt和模型身份的攻击者。
