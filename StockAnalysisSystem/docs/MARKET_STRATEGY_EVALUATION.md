# 冻结 LightGBM 候选：评分与研究回测

本入口只读取已成功的 `lightgbm_all_market` 工程候选，不训练、不发布，也不替换旧模型。保持固定沪深 A 股池及 ID，但每个日期的可评分股票允许因上市、停牌、预热而不同。

## 运行

在 `StockAnalysisSystem/python-services/stock-analysis-app` 下，使用配置好依赖的 Python；把下面任务 ID 替换为自己的成功任务 ID：

```powershell
python scripts/evaluate_market_candidate.py --task-id cf4f07c0bf0948dba20e281214880e3b
```

共享配置从项目 `.env` 读取。项目数据路径由现有 `PREPARATION_DATA_ROOT`、`RUNTIME_PATH_MAP_FILE`、`TRAINING_CALENDAR_FILE`、`TRAINING_ARTIFACT_ROOT` 决定。`--project-root` 可显式指定运行配置/数据所在的 StockAnalysisSystem，不硬编码开发者盘符；默认使用代码所在项目。

每次创建新的 `.runtime/training_tasks/evaluations/<evaluation-id>`。`BOUND` 输出目录，`SCORE` 每 50 只输出进度。程序使用工程资源门槛；中断/超时目录不生成完成封存，不得当作完整结果，也不覆盖或自动续写。

## 数据不足不是回测成功

产物保存逐股无标签评分、资格原因和原始行情、因子/限价，以及 `report.json`、`requirements.parquet`、完整清单哈希及完成封存。

- `READY`：三种固定成本场景及同成交规则的可评分全集基线，明细按场景分文件保存。
- `NOT_READY`：缺数据或持仓估值无法解释，不输出账户收益。完整可补采缺口在 `requirements.parquet`；报告只展示前 100 条和总数。
- `EVALUATION_FAILED`：输入、来源或完整性校验失败；没有完成封存时不得消费部分文件。

评分只使用信号当日以前的特征，**不按未来第 5 日是否有标签筛选**。训练仍保留原来的 T 收盘→SSE 第 5 个交易日收盘标签。评分输入与训练一样先量化为 float32；原始行情和资格校验保持原精度。

`diagnostics` 独立记录事后标签匹配覆盖、逐日 RankIC、十分组标签收益和零预测误差基线；未匹配到未来标签的评分仍保留。这些重叠的未复权标签诊断不是账户收益。换手记录按每日交易前权益归一化，单边换手定义为当日买卖总名义金额除以交易前权益再除以二，再逐日累计；不直接用成交额冒充换手率。

## 补充执行数据

分析代码不联网。先到 `python-services/python-collector` 查看已封存缺口计划：

```powershell
python scripts/collect_execution_evidence.py --requirements ../../.runtime/training_tasks/evaluations/<evaluation-id>/requirements.parquet
```

默认仅核验文件并显示计划，不初始化网络客户端。确认需要补采时显式添加 `--acquire`：

```powershell
python scripts/collect_execution_evidence.py --requirements ../../.runtime/training_tasks/evaluations/<evaluation-id>/requirements.parquet --acquire
```

已有执行限价逐次验哈希后跳过；小缺口只请求缺失股票，大缺口按需取当天回复后仅补仍缺失的股票。响应缺行/未知或零限价不代表“不限涨跌幅”。因子复用既有证据仓，仅查询仍缺失的代码/日期。统一使用 `.runtime/market_requests/request_state.json` 限频、额度冷却及互斥状态；不启动或恢复其他已暂停 worker。

复权因子按原始来源文件分组验哈希，不按股票重复解析同一份全市场文件。某日仍缺至少 100 只时，复用 collector 的 `NativeDayAcquirer` 按日查询一次；不足 100 只时定向查询。按日回复若缺少所需股票，只对剩余股票定向回退。原始回复及请求证明留存，只有实际返回的所需行进入共享证据索引；已完成回复可断点复用，空回复仍记为未解决，不反复重试或推定因子。`PLAN.factor_keys` 是封存需求中的因子键数，不是实际请求次数；实际采集会再次核验共享证据并跳过已补齐的键。

Tushare 的 `stk_limit` 权限和响应范围以[官方接口说明](https://tushare.pro/document/2?doc_id=183)为准。权限不足或来源冲突时保留证据并停止，不能猜涨跌停价或改写旧数据。非因子/限价缺口（例如无法解释的持仓缺报价）需要独立核对，不可用这个入口伪造补齐。

补采结束后重新运行评估，生成另一个新目录。旧评估与旧候选保持封存。

## 固定首轮研究协议

- 原候选测试信号区间用于回放；额外尾部无标签评分单独保存，不扩大回测调参范围。
- Top10，等权，仅做多、无杠杆；每 5 个 SSE 交易日调仓。T 收盘后评分，T+1 开盘计划买，T+6 开盘计划卖。
- 股票不足10只时空余份额保留现金；未成交不按未来结果补选。卖不出的仓位保留、逐日重试，资金不提前释放。
- 开盘涨停不买、开盘跌停不卖；无量或停牌不成交。滑点后价格也必须落在当日可验证价格/限制范围内。
- 停牌缺报价只有明确全天证据才允许陈旧账面估值，且单独标记；不是填造行情或成交价。无法解释的持仓缺口阻断评估。
- gross、双边各3bps费用、双边各3bps费用加双边各10bps滑点；这些是固定研究压力场景，不等于真实历史税费、最低佣金或券商账单。
- 分数型复权收益代理单位，不模拟实盘手数、竞价排队、容量或真实现金分红账本。期末保留未平仓估值，不虚构清仓。

当前冻结池回放不等于历史时点完整市场重建。原测试集已被诊断查看，不能再声称它是从未触碰的最终留出集。参数优化只允许在训练/验证区间单独预登记和执行；现有 `bounded_v1` 含义不变，数据尚未就绪前不自动重训或发布。
