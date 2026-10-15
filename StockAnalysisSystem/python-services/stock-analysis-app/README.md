# 股票分析应用

本服务负责特征工程、模型训练、预测和回测。真实市场数据由 python-collector 统一获取，本服务读取 MySQL 与已有缓存，不调用 Tushare/AkShare。主可视化入口为 Vue frontend；Streamlit 保留为兼容研究看板。

首次配置、空库初始化、服务启动及完整副本恢复，请先阅读 [安装与恢复指南](../../docs/PORTABLE_SETUP.md)。不能照旧文档的 --mode 命令运行，也不要直接删除 models 或来源缓存。

## 当前入口

在 stock-analysis-app 目录，使用已激活环境的 Python：

```powershell
python main.py --help
python main.py train --help
python main.py predict --help
```

| 子命令 | 用途 | 注意事项 |
|---|---|---|
| collect | 从已落库数据构建/增量更新本地面板 | 名字保留兼容，不是外部行情采集 |
| clean | 清洗已落库单股数据 | 不补造缺失行情 |
| feature | 传统或 Transformer 特征计算 | 可能更新本地缓存；传统特征可入库 |
| train | traditional / ranking / transformer 训练 | 显式训练，不是加载已有模型推理 |
| predict | 兼容预测、排名或回测 | 部分分支仍先训练，详见下节 |
| dashboard | 启动兼容 Streamlit 看板 | 不替代 Vue 的只读预测 API |

--use_tushare、--delay 保留旧脚本兼容，不代表分析端联网。--skip_update 选择已有数据库面板；即使未设置，也不应把分析命令当作 Collector 的采集入口。部分旧兼容流程还调用表初始化和幂等保存函数，可能写行情、特征、结果或缓存；它们不是只读 API。首次使用必须先通过正式空库初始化/已有库迁移完成数据库准备。

## 三条独立分析管线

| 管线 | 数据和特征 | 模型与输出 |
|---|---|---|
| traditional | 单股因果技术指标 | XGBoost 分类/回归；日期验证回测仅支持二分类 |
| ranking | 固定股票池面板、传统技术指标 | LightGBM 排名及日期约束滚动回测 |
| transformer | 独立 Alpha/技术/截面特征和序列 | Transformer 训练、排名与独立工件验收 |

传统特征与 Transformer 特征不是同一张特征表，不能因名称相似直接合并。传统两个入库入口共用 data_processor/feature_schema.py 的 62 项映射；未来收益和方向标签不入库。

Transformer 的配置名 158+39 是历史标识：当前 feature_columns 返回 203 个模型输入列；39 配置为 38 个模型输入列，instrument 单独用作股票映射。计算出来的 cs_turnover_pct 不在当前模型列契约中。本轮不改变维度、顺序、映射或旧模型；如果要纳入新特征，须建立新版本并重新训练，不能原地覆盖旧工件。

## 示例命令

以下为显式操作示例，不会因安装或启动自动执行。股票池、历史范围、日历和数据完整性需先验收。

传统特征：

```powershell
python main.py feature --pipeline traditional --skip_update
```

单股兼容训练：

```powershell
python main.py train --pipeline traditional --stock 000001.SZ --model xgboost --skip_update
```

日期约束策略验证（将日历路径替换为实际可信文件）：

```powershell
python main.py train --pipeline traditional --stock 000001.SZ --model xgboost --backtest --skip_update --calendar PATH_TO_TRUSTED_CALENDAR.json
python main.py train --pipeline ranking --index 000300.SH --skip_update --calendar PATH_TO_TRUSTED_CALENDAR.json --top_n 10
python main.py predict --pipeline ranking --index 000300.SH --backtest --skip_update --calendar PATH_TO_TRUSTED_CALENDAR.json --top_n 10 --train_window 365 --rebalance_days 5
```

ranking 的 train、普通 predict，以及 traditional 未指定股票的批量 predict，目前都可能执行训练；不要将它们当作冻结模型的只读推理。LSTM 训练未实现，已从 CLI 可选模型中移除；旧 API/工件的不支持错误仍明确保留，不会偷偷改用其他模型。

Transformer 特征、训练和预测分别使用：

```powershell
python main.py feature --pipeline transformer --index 000300.SH --skip_update
python main.py train --pipeline transformer --index 000300.SH --skip_update --transformer_features 158+39
python main.py predict --pipeline transformer --index 000300.SH --skip_update --transformer_features 158+39 --top_n 5
```

真实运行前核对输出目录与股票映射；不要用另一个池或不同特征维度覆盖受审计产物。已验收的固定股票池训练/推理/组合回测还有 scripts 下的独立 runner，见相应脚本指南，不应机械地用通用命令替代其冻结协议。

## 正式单股预测 API 与滚动评估

正式单股回归发布使用 scripts.forecast 的显式 train / predict 子命令。predict 加载指定的既有模型，不重新训练；它与 main.py 的兼容分类研究入口不是同一协议。可信日历格式、固定特征、源记录连续性、参数与发布边界见 [V0.7 单股预测指南](../../docs/V0.7_SINGLE_STOCK_PREDICTION.md)。

```powershell
python -m scripts.forecast --help
python -m scripts.evaluate --help
python -m uvicorn api.forecast:create_app --factory --host 127.0.0.1 --port 8084
```

GET 不训练、不发布、不采集、不建表。缺少数据、模型、报告或有效摘要时明确报告不可用，不展示假预测。V0.8 固定协议和只读核对见 [评估指南](../../docs/V0.8_EVALUATION_GUIDE.md)。

## 兼容 Streamlit 回测

```powershell
python main.py dashboard
```

模型预测信号按钮需要上传由真实采集得到的交易日历 JSON。其格式为 source=tushare.trade_cal、exchange=SSE/SZSE、start/end，以及覆盖每个自然日的 rows（cal_date、整数 is_open）；必须覆盖行情历史和点击当天。它不是 V0.7 的 open_dates 格式，不能混用或用工作日猜测。

点击后使用与 CLI 相同的上海时区截止规则：严格晚于 16:00 才选当天，休市或缺数据时按可信日历选择/回退；历史开市日缺口明确拒绝。模型使用净化的训练/验证切分，只对未见日期记账，并展示目标日、实际截止和已见日期。不会自动采集或保存/替换旧模型。图表买卖标记使用累计收益率坐标，成交价格在悬浮提示和交易表中显示。

这是未复权价格上的研究回测，手续费、滑点、板块交易限制与真实执行之间仍有边界；不等于可直接实盘部署。[策略验证说明](scripts/README_strategy_validation.md)

## 缓存、模型与恢复

- 特征缓存保存原始量纲，标准化只使用训练数据拟合的 scaler；推理使用对应模型 scaler。
- 推理保留最新无标签日期，不把旧停牌日期冒充当天预测。
- 旧模型、缓存、来源索引和回执可能被下游 SHA256 清单引用，不能只凭目录小或实验中断就删除。
- 缓存口径不符时保留原始证据，按 [特征管线说明](../../docs/TRANSFORMER_FEATURE_PIPELINE.md) 在新工件目录重建/训练，不修改既有哈希绑定文件。
- 完整跨机恢复还需独立共享限频目录及明确旧根映射；两台机器不得同时跑 worker。

本项目用于学习和研究，不构成投资建议。

## 公共原语与有意保留的边界

Transformer runner 的通用输出目录检查和旧工件审计集中在 `analysis/artifact_audit.py`。新清单使用 POSIX 相对路径；读取旧 Windows 反斜杠清单时安全解析，但不改写原清单或其身份摘要。源与输出不得相互嵌套，工件路径不得逃出模型根。前向信号入口的固定冻结组绑定仍单独保留，不放宽为任意输出目录。

正式单股预测与滚动评估共用 `analysis/regression_metrics.py`，对空、非有限及不配对观测明确拒绝；常量真值的 R² 为 `null`。旧分类研究入口的 sklearn 指标和排名指标是不同契约，不混用。

传统/Transformer 特征管线、不同执行假设的回测账本、历史验收脚本及受哈希保护的模型证据有实际用途，不因年代或命名相似而删除。
