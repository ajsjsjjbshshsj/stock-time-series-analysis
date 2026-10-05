# Transformer 最新历史训练与冻结

独立离线入口，用现有截至2026-09-30的20股行情和因子重新训练6个模型。
两价格模式adjusted/unadjusted_control，各seed42/123/2026；不择优、不搜索参数。
CUDA、203固定特征、batch8、最多30轮、patience5，沿用实际审计配置。
旧模型、缓存和历史资金回测全部保留，不部署、不写数据库、不采集、不推送。

## 运行（stock-analysis-app目录，每条单行）

```powershell
D:/Python/python.exe scripts/run_transformer_forward_freeze.py --stage run --source-experiment models/transformer/multiseed_20261005 --output models/transformer/forward_freeze_20261005
```

```powershell
D:/Python/python.exe scripts/run_transformer_forward_freeze.py --stage verify --source-experiment models/transformer/multiseed_20261005 --output models/transformer/forward_freeze_20261005
```

输出必须独立；部分失败目录保留并拒绝覆盖。已冻结完整输出重跑run只验收，
不重训、不改写模型/报告/清单。verify读取真实文件、缓存、配置和scaler重新校验。

## 训练边界与阅读顺序

1. analysis/transformer_forward_freeze.py：6身份、实际配置继承、数据截止、purge边界和固定交易规则。
2. analysis/transformer_forward_diagnostic.py：无标签末日推理，冻结scaler只transform一次，不创建图表。
3. scripts/run_transformer_forward_freeze.py：来源只读审计、6模型训练、实际文件/标签/scaler统计验收及冻结。
4. 三个test_transformer_forward_*测试文件：日期跨界、缓存尺度、模型与配置篡改、部分失败和只读续跑。
5. 实验目录SUMMARY.md→report.json→freeze_manifest.json→各seed/mode/result.json及scores.json。

验证边界为2026-07-30。训练标签目标日必须早于该边界；验证标签只能使用已完成的历史数据。
scaler仅fit训练成熟行，不fit验证或无标签尾部；最后5共同交易日保留特征但没有监督标签。
报告分别写行情末日、监督信号末日和目标末日，不能把行情末日误认为训练标签末日。
checkpoint按原验证final_score/早停选择，这不是未来收益，不用于挑选最赚钱的模型。
原始特征缓存不标准化，完整历史计算EMA/EWM/OBV，保留模型历史起点、股票映射及复权因子契约。

## 冻结不等于盲测已经完成

6个9月30日输出是historical_diagnostic，不是前向信号、收益概率或投资建议。
冻结只在6模型验收齐全后生成，清单绑定来源、配置、数据切分、模型/scaler及策略文件哈希。
raw为主要排序、nonnegative_variance次要对照，Top5等权、T+1买/T+5卖、4交易间隔、100万元；
四成本情景及同成本20股等权基准全部保留，不据历史结果挑选种子/价格模式/费用。

后续必须明确未来数据接入与信号记录协议；信号须冻结后且买入窗口开始前保存。
迟到或事后补算不算盲测，新因子必须沿用冻结历史复权锚点，不可更换尺度直接喂旧scaler。
本入口没有自动采集/订单/前端接入，也没有未来评估起止期；这些是下一阶段工作。
成本为假设，可分割复权资产单位不是真实股数；没有整手/最低佣金/税费/涨跌停/停牌/队列/
容量模拟。固定20股有选择偏差，3个种子不是3个独立市场，不能据本轮训练承诺预测效果。
