# Transformer 行情驱动资金回测

独立离线入口，不用旧ranking_backtester中“预测值×0.2”的近似收益，不重训、不采集、
不写数据库或替换线上模型。已看过的6—8月及9月尾仓仍为历史诊断，不是新盲测。

## 执行（每条均为单行）

在stock-analysis-app目录运行，需要现有Python环境；首次分数生成用CUDA。

```powershell
D:/Python/python.exe scripts/run_transformer_portfolio_backtest.py --stage run --source-experiment models/transformer/multiseed_20261005 --output models/transformer/portfolio_backtest_20261005
```

```powershell
D:/Python/python.exe scripts/run_transformer_portfolio_backtest.py --stage verify --source-experiment models/transformer/multiseed_20261005 --output models/transformer/portfolio_backtest_20261005
```

初始资金默认100万元，--initial-capital可显式改变；同一输出目录不能换参数。
每个来源checkpoint沿用原训练配置、scaler、203列順序及历史起点。三个seed和两
训练模式均完整保留，raw排序为主要策略，nonnegative_variance只作次要对照。

## 资金口径

T收盘生成信号，T+1开盘等权买Top5，T+5开盘全卖；下一信号在T+4收盘，下一
买入与上轮卖出同在T+5开盘。先卖后买，每4交易间隔调仓，跨月不重置，不叠加
重复资金。最后8月信号允许9月按期退出。持仓每天按真实共同复权close估值。

买入单位数=等权预算/[open×(1+滑点)×(1+买费)]；卖出收入=单位数×open×
(1-滑点)×(1-卖费)。成本从本金中扣除，绝不借钱补手续费。相同股票也全卖再买、
按双边收费。换手分母为交易当天买卖之前的开盘权益，双边成交额/权益及其一半
的单边换手都保留；累计换手是日比值求和，不称作年化换手。

成本情景均是假设：gross(0费0滑点)、fee3(双边各3bp、0滑点)、fee3_slip5
(双边各3bp、每次5bp滑点)、fee3_slip10(双边各3bp、每次10bp滑点)。1bp=0.0001。
不是实际券商报价或法定税率，未构建最低佣金/税费规则。同成本20股等权基准采用
完全相同交易时点和现金预算。共3seed×2模式×2策略×4情景=48个组合。

复权可分割资产单位是收益代理，不是原始股数/分红现金账本。未模拟整手、停牌/
涨跌停可成交性、订单队列、容量约束；开盘报价不保证真实成交。不能直接实盘使用。

## 阅读顺序

1. analysis/portfolio_backtest.py：纯价格/现金账本、调仓日程、每日净值、费用和回撤。
2. analysis/transformer_portfolio_signals.py：无标签推理，不因未来标签缺失丢掉日期；
   每个窗口只含截至信号日的共同历史交易日，冻结原model/scaler。
3. scripts/run_transformer_portfolio_backtest.py：只读来源审计、分数缓存、历史Top5收益
   对齐检查、48资金组合/同成本基准、按3seed汇总和独立重算验收。
4. tests/test_portfolio_backtest.py及另外两个portfolio测试：独立手算与异常输入防护。
5. 输出SUMMARY.md→report.json→portfolios/seed_*/模式/排序/费用/result.json；
   signals保存逐股分数与哈希，benchmarks保存4个同成本基准，verification保存验收。

首笔费用/首日亏损与持仓中下跌均纳入回撤，初始本金包含在高水位；月底未平仓
也估值，逐月收益由真实净值边界计算。完整结果保留，不选最优seed/成本场景。
只有3个初始化seed，样本SD使用ddof=1；不能把48情景当48个独立市场实验。

完整子结果续跑会核对来源/配置/分数哈希和账本重算结果；部分失败保留并拒绝覆盖。
全部完成时--stage run只作验收，不重新推理或覆盖账本。规划和实验产物不提交git。
