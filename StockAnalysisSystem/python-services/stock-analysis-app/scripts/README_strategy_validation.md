# 三条策略验证基础：本轮修复与阅读指南

本轮修复既有单股、LightGBM排名的验证基础，保留已验收Transformer路径。
三条管线各自上线，不合成一个策略。尚未接真实运行按钮/后台采集任务，不宣称上线或收益有效。

## 日期与数据

analysis/strategy_dates.py：输入有时区request_time、可信本地交易日历、固定股票池和完整行情；
转换北京时间，严格大于16:00才选当天，等于16:00仍选上一已完成交易日。
休市/长假不按日历昨天或工作日猜测；没有覆盖当前日期的日历直接失败。
缺某只股票或必需因子/字段时回退最近完整交易日，分别保存target_trade_date、data_cutoff、
fallback_reason。完整性选择不是完整历史连续性证明，本地入口另核对历史开市日与股票池。
本轮只选已有数据，不调用Tushare，也不读取Token；真实增量采集/限频/警告展示在下一阶段接入。

calendar JSON形状：source=tushare.trade_cal，exchange=SSE或SZSE，start/end为ISO日期，
rows为该范围每个日历日完整且升序的{cal_date:ISO日期,is_open:整数0或1}；
须来自真实trade_cal采集，不通过示例伪造交易日。覆盖完整行情历史和当前时钟。

## 推荐阅读顺序

1. strategy_dates.py：目标日/实际截止与安全回退原因。
2. strategy_samples.py：日期样本、未来标签到期、整日切分与边界purge；目标日期不是模型特征。
3. backtester.py：T收盘信号→T+1开盘成交、收盘估值、费用预算与初始回撤基线。
4. predictor.py的train_dated/prepare_inference_frame：小于已见标签边界的信号不用于样本外收益；未知标签不删最新推理行。
5. ranking_predictor.py：训练/验证/测试整日隔离，探针只看训练，早停只用验证；真实测试指标不参与选择。
6. ranking_accounting.py：TopN正分权重只决定配置，真实价格决定盈亏；同费用、同池、同周期基准。
7. ranking_backtester.py：每个信号日只用已到期训练标签；没有全数据预筛选，没有预测值×0.2收益。
8. main.py：显式传当前模型路径、完整价格和可信日历；结果带data_selection。

## 本地入口（单行）

在stock-analysis-app目录，使用实际可信日历路径替换下面的路径：

```powershell
D:/Python/python.exe main.py predict --pipeline ranking --backtest --skip_update --calendar PATH_TO_TRUSTED_CALENDAR.json --top_n 10 --train_window 365 --rebalance_days 5
```

该入口在各预定信号日进行LightGBM滚动训练来验证策略，不加载/覆盖生产latest，不联网。
这不是仅预测的冻结模型入口；真实运行可能耗时，需要完整特征和足够成熟历史。

```powershell
D:/Python/python.exe main.py train --pipeline ranking --skip_update --calendar PATH_TO_TRUSTED_CALENDAR.json --top_n 10
```

该入口主动训练并保存新模型，再预测最新无标签日；旧同名产物不会覆盖。不是本轮自动执行的操作。
已训练模型离线验证可使用run_ranking_backtest，需明确model_path、完整prices和calendar。
旧predict ranking命令仍会训练；下一阶段按钮会区分验证、训练与加载既有模型预测，不隐藏重训。

单股验证函数run_backtest(df,stock_code='000001.SZ',calendar=...)须显式指定原请求股票，分别返回技术信号与日期约束XGBoost回测、训练证据和data_selection。
旧匿名数组训练入口保留兼容但没有自动增加因果证据，不应替代train_dated进行策略验收。
单股概率回测仅二分类概率可用，回归收益不是概率。旧LSTM选项没有train_lstm实现，
现在明确拒绝，不默默换模型；完整LSTM训练/日期序列适配需单独完成。

Transformer继续使用README_transformer_portfolio_backtest.md的已验收独立入口，
固定模型/scaler/费用/规则及冻结来源保持不变；迟到诊断不会改标成及时盲测。

## 旧产物与局限

旧模型、Parquet缓存、报告、数据库、已发布信号均保留。传统验证缓存另存validation_v1；
没有完整来源证明的增量缓存不混用，按完整提供的原始历史重建，可能增加计算时间。
旧模型缺少标签到期/验证已见范围时，样本外回测拒绝；不能仅修改元数据就让旧模型合格，
需要以后重新训练为新产物。本轮只修代码和跑固定小样本测试，没有训练真实市场模型。

验证入口使用strict_validation数据库读取，不使用旧未证明的raw_panel补换手率。
请求股票池从stock_basic/成分筛选单独确定；缺整只股票或特征计算丢行立即失败，
不能用“当前返回的股票”重新定义原股票池。排名还要求真实daily_basic换手率齐备。
--skip_update保持兼容，但验证分析始终只读数据库，采集仍由collector负责。
独立特征面板必须与完整价格日期/股票/close逐项匹配，不能用少一行的shift改变标签期限。

实际CLI结果会打印STRATEGY_REPORT路径与指标/截止摘要，每次保存到新的
reports/strategy_validation/<时间_UUID>/result.json；可用--report_dir指定新报告父目录。
报告含资金曲线、成交、假设、模型元数据和data_selection；旧报告不覆盖。
单股--backtest只接受二分类xgboost，回归/多分类请求在读DB前明确拒绝，不偷换模型。

单股使用95%资金与整数研究股数；排名使用可分割研究单位。当前只模拟给定费率/滑点，
没有100股整手、最低佣金、税费分项、停牌、涨跌停排队、容量或实股分红账本。
原始价格输入不能证明跨除权经济收益；要评价复权结果必须提供完整一致的复权价格证据，
不能偷偷给旧原始价格报告加“复权”标签。已有Transformer复权实验口径继续独立保留。
修复通过仅证明计算与边界正确，不证明策略盈利，不自动选优/部署/下单或推送代码。
