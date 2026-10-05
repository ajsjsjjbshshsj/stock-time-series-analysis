# Transformer 三种子敏感性验证

这是离线诊断，不接生产 API、不写数据库、不替换线上模型、不采集新数据。
在 `stock-analysis-app` 目录执行，复用真实20股行情及复权因子快照。

## 运行（单行命令）

```powershell
D:/Python/python.exe scripts/run_transformer_multiseed.py --stage run --source-experiment models/transformer/fixed_features_20261004 --output models/transformer/multiseed_20261005
```

```powershell
D:/Python/python.exe scripts/run_transformer_multiseed.py --stage verify --source-experiment models/transformer/fixed_features_20261004 --output models/transformer/multiseed_20261005
```

6个seed42模型只读复用；seed123、2026各训练6个模型。月份为2026年6/7/8月，
两组为复权行情和未复权对照，全部使用同序203特征、相同训练设置。只改变seed。
每月只用之前的数据训练，各模型独立拟合scaler；评估统一使用真实复权收益标签。
需要CUDA，最多30轮，batch8、patience5；CPU特征计算4进程。不启用AMP。

## 阅读顺序

1. `analysis/transformer_multiseed.py`：先逐seed汇总相同65个成熟日期，再算3个seed均值、
   样本标准差(ddof=1)、极值、正超额seed个数，以及逐月/组间配对差值。
2. `scripts/run_transformer_multiseed.py`：只读源验证、哈希绑定、种子配置、续跑和验收。
3. `tests/test_transformer_multiseed.py`：手算统计例子与异常输入/来源/产物防护。
4. 输出目录的`SUMMARY.md`、`report.json`、`verification.json`：真实训练结果与验收证据。
   `seed_*/模式/月/daily_metrics.json`保留逐日指标；`result.json`保留配置、seed、模型/
   scaler/预处理哈希及复用或训练来源。seed42文件引用旧模型，不复制或修改旧模型。

原始排序是主要指标，nonnegative_variance是次要对照，旧调整仅诊断。
等权基线使用共同复权标签；动量排序沿用各模式自己的价格历史，所以动量基线在
同一模式的三个seed之间一致，但不强制复权/未复权两组的动量排序相同。
不要挑表现最好的seed。3个初始化种子不等于3个独立市场，195个seed×日期不是195个
独立市场样本；重叠T+1开盘至T+5开盘收益的均值不是累计收益，不计算可靠显著性。
6—8月是已知诊断月份，不是新盲测。股票池偏差、数据修订、单一市场区间、无交易
费用及成交/资金约束等局限仍存在。预测有效性需要另外的样本外验证。

完整已完成fold可验证后续跑；部分失败目录保留并拒绝覆盖，要重新运行需明确使用新
`multiseed_*`目录。源配置、特征順序、模型/scaler/预处理、日期、指标或数据哈希不匹配
均停止，不静默跳过。规划及模型产物不加入git，业务代码、测试和本说明可本地提交。
