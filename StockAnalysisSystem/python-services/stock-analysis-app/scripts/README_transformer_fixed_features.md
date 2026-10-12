# 非负评分与固定特征实验

这是离线诊断，不替换线上模型，不采集新数据，不写数据库。旧模型仍默认legacy_variance；新模型显式绑定nonnegative_variance，预测与评估共用analysis/transformer_scoring.py。

新规则先将当日排序分数归一化到0～1，再按三辅助头分歧打折。分数不是收益或概率；平分时返回0，稳定保留股票顺序。旧算法原值不变，新策略不可静默混读旧模型；旧模型离线消融通过显式运行时覆盖实现，不改旧sidecar。

阅读顺序：transformer_scoring.py（评分）、transformer_features.py（模型策略绑定）、transformer_experiment.py（同一checkpoint三种评分）、run_transformer_fixed_features.py（旧模型消融与六个固定特征训练折）。固定特征训练关闭探针，两组使用相同203列与顺序，scaler各自只拟合训练段。

从stock-analysis-app目录运行，每条命令均为单行：

```powershell
D:\Python\python.exe scripts/run_transformer_fixed_features.py --stage ablate --source-experiment models/transformer/adjusted_experiment_20261004_token_retry --output models/transformer/fixed_features_20261004
```

```powershell
D:\Python\python.exe scripts/run_transformer_fixed_features.py --stage train --source-experiment models/transformer/adjusted_experiment_20261004_token_retry --output models/transformer/fixed_features_20261004
```

```powershell
D:\Python\python.exe scripts/run_transformer_fixed_features.py --stage verify --source-experiment models/transformer/adjusted_experiment_20261004_token_retry --output models/transformer/fixed_features_20261004
```

先看ablation_report.json比较旧checkpoint仅改变评分的效果，再看fixed_report.json比较全特征训练；每个模型同时报告原始排序、旧调整、新调整，避免把两项改动混在一个结果中。policy_*指标统一使用真实复权收益标签；common_adjusted_label_raw_top5_return是原始排序的共同口径基线。

验收检查旧产物哈希、原报告与源数据、8个消融折和6个训练折的唯一身份、模型/scaler口径、sidecar实际203列顺序、每个策略逐日有限值、超额与共同标签的一致性、日期全覆盖与汇总一致性；部分失败保留，不覆盖重训。

9月预测按策略保存CSV，并在该折记录中保存CSV哈希、推理日期、源快照哈希、checkpoint哈希及策略。验收检查股票属于模型且在最新交易日存在、排名和分数顺序正确；复制过期CSV或替换策略文件会失败。离线预测必须create_plots=False，不能写回旧模型图表目录。

6—8月已看过结果，仅作诊断；9月只复用旧模型作已知回归。单seed42不能证明稳健性，固定20股有选择偏差，输入价格和标签同时改变不能分别归因。未模拟交易费用与成交约束，重叠区间均值不是累计收益。不按评估月结果挑参数或部署模型。
