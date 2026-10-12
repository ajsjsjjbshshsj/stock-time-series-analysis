# Transformer 复权对照实验

这是独立离线实验，不替换 API 模型、不写 MySQL、不删除旧训练产物。原目标是 T+1 开盘到 T+5 开盘收益排序，不是下一日价格预测。

## 阅读顺序

1. `../../python-collector/app/market_data/factor_snapshot.py`：真实因子采集、逐请求暂停、分钟冷却、小时配额停止与断点缓存。
2. `../data_processor/adjusted_market_panel.py`：固定历史基准因子、OHLC 复权、真实量额保留、单位正确的模型 VWAP。
3. `../analysis/transformer_features.py`：复权口径绑定到 raw 缓存和模型/scaler sidecar，旧模式默认兼容，不允许混用。
4. `../data_processor/probe_selection.py`：按唯一日期切分，并清除跨越探针验证起点的训练标签。
5. `../analysis/training_control.py`：可选早停，默认关闭；本次显式设置耐心5轮。
6. `../analysis/transformer_experiment.py`：逐日指标、生产一致的三辅助头方差调整，以及两组使用共同复权标签的比较。
7. `run_transformer_adjusted_experiment.py`：串联四个历史月份、两种价格模式，各自重新训练，不共享 scaler、探针或权重。

## 执行

工作目录为 `StockAnalysisSystem/python-services/stock-analysis-app`。以下每条命令均为一行，使用现有 Python 环境。

```powershell
D:\Python\python.exe scripts/run_transformer_adjusted_experiment.py --stage collect --source models/transformer/pilot_20261004_1814/snapshot.parquet --output models/transformer/adjusted_experiment_20261004_2020
```

采集阶段由独立 `python-collector` 进程执行，分析服务没有行情 SDK 导入。来源日线需有匹配的 `collection_manifest.json` 哈希。因子逐股按年分片，缺失、重复或非正值会停止，不用模拟值补齐。

```powershell
D:\Python\python.exe scripts/run_transformer_adjusted_experiment.py --stage run --source models/transformer/pilot_20261004_1814/snapshot.parquet --output models/transformer/adjusted_experiment_20261004_2020
```

需要可用 CUDA。固定最多30轮、batch8、seed42、早停耐心5轮。2026年6/7/8月是固定历史诊断窗口，9月是已经看过结果的回归对照，不称为全新盲测。绘图使用 Agg，不弹出阻塞窗口。

```powershell
D:\Python\python.exe scripts/run_transformer_adjusted_experiment.py --stage verify --source models/transformer/pilot_20261004_1814/snapshot.parquet --output models/transformer/adjusted_experiment_20261004_2020
```

只有八个模型结果、匹配模型/scaler哈希、逐日指标、最新预测以及旧文件完整性核验齐全时，才会产生通过验证的报告。训练失败的部分目录不会被自动覆盖；完成的折可复用，因子分片可续取。

## 配额和结果解释

如果接口返回每小时配额，采集停止并在 `factor_shards/acquisition.json` 保留 `retry_not_before`。冷却期间重跑不会再发接口请求；不要删除该文件绕过配额。权限不足同样直接停止，不用短重试碰运气。

真实运行完成后，阅读 `report.json`、`verification.json` 和各折 `daily_metrics.json`。比较两组时使用 `common_adjusted_label_*`：两组自己的训练标签收益口径不同，不能直接把它们相减当作性能改善。`raw_top5_return` 和 `adjusted_top5_return` 分别对应原始排序头和不确定性调整后的排名，不混为一项。

报告是重叠区间标签收益均值，不是累计组合收益；未模拟手续费、分红现金处理、涨跌停成交和资金约束。股票池还有完整历史筛选偏差。无论结果好坏都完整记录，不能把代码运行成功或单次改善称为预测有效。
