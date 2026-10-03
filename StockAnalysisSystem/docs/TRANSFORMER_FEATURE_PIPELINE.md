# Transformer 特征缓存、训练与最新日期推理

本次修复针对旧 Transformer 排名链路，不改 V0.7 单股预测或 V0.8 评估 API。代码正确性测试使用合成行情和临时目录，不代表真实股票预测效果提升；本次没有启动真实行情训练、替换已有模型或删除用户缓存。

## 建议阅读顺序

1. `python-services/stock-analysis-app/analysis/transformer_features.py`：先看 `normalize_panel`、`build_feature_panel`，再看缓存保存/加载、训练拆分和模型预处理绑定。
2. `analysis/transformer_trainer.py`：公开入口为 `compute_and_save_features`、`run_transformer_training`、`predict_top_stocks_transformer`。
3. `analysis/transformer_utils.py`：指标公式和 `create_ranking_dataset_vectorized`，窗口按每只股票已经观测到的交易会话取历史，不要求自然日连续。
4. `tests/test_transformer_features.py`、`tests/test_transformer_inference.py`、`tests/test_transformer_dataset.py`：分别验证缓存/完整历史、训练推理边界、交易日序列。包含临时小模型的一轮合成训练与预测闭环。

## 缓存：只存原始量纲

`features_*.parquet` 保留最新日期；最后五条没有未来价格的记录仍保存，`label` 为 NaN。元信息要求 `cache_version=2`、`feature_scale=raw` 和明确的特征顺序。缓存旁的 `raw_panel.parquet` 保存合并后的原始行情，供重新计算。

增量是“原始行情按股票/日期合并，新输入覆盖重复键，然后完整重算保留历史”，不是“80 天预热后只追加新特征”。默认训练特征保留最近三年；完整历史计算确保 EWM、OBV、EMA 与全量路径一致，验证边界也不会重启指标。代价是计算和内存开销较大，但新旧行不会混用原始值与 z-score。

`is_val` 每次按当前验证起点重新生成布尔值，不依赖旧缓存标记。股票索引由缓存股票集合确定；推理使用模型冻结的映射，不根据新股票集合重新编号。

旧的标准化缓存没有可靠的原始量纲声明，加载会明确报错要求重建。不要继续复用旧 Transformer 权重；它们应与新版数据管线重新训练。旧文件不会被自动删除。重新计算时建议选新的隔离目录，并提供完整原始行情；若直接调用公开 compute 入口且目录已有 raw 缓存，它会执行合并重建，而不是清空历史。

## 训练：成熟标签与时间清除

标签维持原公式 `(open[t+5] - open[t+1]) / (open[t+1] + 1e-12)`，不是“下一交易日收盘价”。`label_target_date` 表示第五个未来观测交易会话的日期。

验证起点是面板最新日期往前两个月。训练行必须有有限标签、日期早于验证起点，而且 `label_target_date` 严格早于验证起点；跨界标签被清除，避免把验证区间的价格用于训练。

StandardScaler 只在这些训练行上拟合；全量/缓存入口使用相同逻辑。验证和无标签最新行仅 transform。验证序列保留训练区间已知的特征历史，包括被清除标签的边界行，但这些行不能充当目标。探针筛选在完整列标准化后进行，模型元信息固定最终子集。

## 推理：模型与 scaler 成对

每个 `<name>.pth` 配套 `<name>_scaler.pkl` 和 `<name>_preprocessing.json`，绑定模型/scaler 文件 SHA256、结构配置、完整特征顺序、选中特征、股票映射和历史计算起点。缺文件、列顺序不符、配置不同或文件混搭均报错；不会新拟合 scaler，也不会改用缓存 scaler。

面板和 feature-path 推理都从原始行情构建特征，跳过标签要求，使用模型的固定历史起点，随后只标准化一次。feature-path 推理读取相邻 `raw_panel.parquet`，并先校验缓存格式；不能只复制 features 文件而漏掉 raw 历史。固定起点防止训练后滚动截断造成 OBV 起点变化。未知股票排除，最新日期落后的股票不会用旧窗口冒充当前预测；历史不足一个序列也排除。

该“最新”指输入数据中的最新观测日期，并不自动联网确认市场最新已完成交易日。缺失交易会话和复权口径仍需要在数据采集/真实训练验收前核对。

## 本地验证

在 `StockAnalysisSystem/python-services/stock-analysis-app` 目录运行（单行）：

```powershell
D:\Python\python.exe -m pytest tests/test_transformer_features.py tests/test_transformer_inference.py tests/test_transformer_dataset.py -q
```

真实重训下一步应先确定股票池、行情复权和历史区间，再在独立输出目录训练并进行未见数据的时间外评估；不能用本次合成测试结论替代预测效果验收。
