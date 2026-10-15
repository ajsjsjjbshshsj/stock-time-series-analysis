# 分析端代码阅读指南

本指南对应当前子命令式 CLI，不使用旧 --mode 分发。项目不依赖 path_config.py；模块根由入口位置和既有包导入规则确定。先完成 [安装与恢复](../../docs/PORTABLE_SETUP.md)，再读业务链路。

## 先分清职责

- python-collector：外部 SDK、限频、缺口补采、来源证据和入库。
- stock-analysis-app：读取数据库/兼容缓存，计算特征、显式训练、预测和回测。
- Vue frontend：当前主可视化界面；api.forecast 是只读预测/评估 API。
- visualization/dashboard.py：保留的 Streamlit 研究界面，不与 Vue 功能逐项等价。
- 旧兼容保存流程仍可能调用幂等数据库保存/初始化；不要把训练命令或旧兼容 CLI 当成只读 API。

## 推荐阅读顺序

| 顺序 | 文件 | 要看什么 |
|---|---|---|
| 1 | main.py | collect / clean / feature / train / predict / dashboard 分发与实际副作用 |
| 2 | config/settings.py、database/db_connector.py | 环境配置、唯一数据库连接入口、会话和显式初始化 |
| 3 | data_loader/collector.py、database/repository.py | 数据库读取及旧缓存兼容，不是外部 SDK |
| 4 | data_processor/panel_builder.py、stock_filter.py | 股票池选择、面板、换手率恢复及传统特征缓存 |
| 5 | data_processor/feature_engineer.py、feature_schema.py | 技术指标与共用的 62 项数据库特征映射；未来标签不入库 |
| 6 | analysis/strategy_dates.py、strategy_selection.py | 可信交易日历、16:00 截止、固定池和完整历史验证 |
| 7 | analysis/strategy_samples.py、predictor.py | 日期标签到期、purge、train_dated 与未见数据约束 |
| 8 | analysis/backtester.py、dashboard_backtest.py | T 收盘信号、T+1 开盘执行、费用与 UI 的显式模型研究 |
| 9 | analysis/ranking_predictor.py、ranking_accounting.py、ranking_backtester.py | 排名、权重账本和逐日滚动训练，不能用全历史预筛选 |
| 10 | analysis/transformer_utils.py、transformer_features.py | 独立特征、原始量纲缓存、成熟标签与 scaler |
| 11 | analysis/transformer_trainer.py、transformer_model.py | 模型训练、推理与序列契约 |
| 12 | analysis/forecast/、analysis/evaluation/、api/ | 正式预测/固定协议评估、不可变发布和只读查询 |

main.load_strategy_calendar、main.select_strategy_snapshot 仍作为兼容导出保留，实现统一在 strategy_selection.py；panel_builder.FEATURE_COL_MAP 仍保留兼容导出，实现统一在 feature_schema.py。不要复制另一份规则来扩展按钮或新入口。

## 三条管线如何读

### traditional

main.py 的 train/predict 命令分发到单股或兼容批量模型。匿名数组训练函数保留旧用途，但不自动提供因果验证证据。策略验收应跟踪 train_dated → purged_splits → run_prediction_backtest，不能绕过 require_unseen。

正式 V0.7 回归模型另外使用 scripts.forecast 的显式训练/冻结模型推理入口，见 [V0.7 指南](../../docs/V0.7_SINGLE_STOCK_PREDICTION.md)。不要把分类概率当成收益率。

### ranking

面板和传统指标 → LightGBM 训练 → 最新无标签日排名，或逐信号日滚动训练 → 真实价格账本。训练与验证/测试按日期隔离；股票数量相同不表示股票映射一致。普通 predict ranking 仍可能训练，不是仅加载模型。

### transformer

原始面板 → 每股完整历史特征 → 截面特征 → 成熟标签及净化分割 → 训练 scaler → 序列模型。推理保留无标签上下文，使用模型的原 scaler。

配置名 158+39 当前对应 203 个模型输入列，不是把名字中的数字相加；39 配置当前为 38 个模型输入列，instrument 另用于身份映射。cs_turnover_pct 虽然被计算，尚未纳入现有模型列契约。改变列数/顺序须版本化，不能使旧模型输入静默漂移。

固定特征、复权实验、multiseed、forward freeze/signals 和组合回测 runner 受各自协议及历史 SHA 清单约束，不能按“看起来重复”直接合并协议或删除旧工件。[特征管线说明](../../docs/TRANSFORMER_FEATURE_PIPELINE.md)

## 运行与检查

从本应用目录查看当前支持参数，不先启动真实采集或训练：

```powershell
python main.py --help
python main.py feature --help
python main.py train --help
python main.py predict --help
```

策略运行示例与日历 schema 见 [策略验证说明](scripts/README_strategy_validation.md)。训练/预测/回测命令须区分实际副作用，不把所有名为 predict 的旧入口当成只读推理。

## 保留边界

- LSTM 训练未实现，CLI 不再提供该选项；旧工件/API 的明确拒绝仍保留，不用其他模型替代。
- 两套特征工程及不同回测账本有不同字段/执行契约，不能仅为缩短代码而合并。
- 旧 baseline/验收脚本和测试未因使用频率低就删除；缺少历史基线导致 skip 不代表验收通过。
- 框架注入、SPI 和独立命令入口不能靠“类名零引用”判为无用。
- 模型、原始来源、回执和限频状态单独备份；新测试、规划、真实 .env、私人启动脚本及运行产物不属于业务代码提交。

新增回归放本地隔离目录验证，已有测试仍可按各模块说明手动运行。清理代码不等于重新训练、补采或重写历史证据。
