# 三模型工作流：训练、使用与发布

本功能不要求策略盈利。`COMPLETE` 表示结果计算及完整性校验成功，不表示策略有效；负收益、低 RankIC 或未定义的相关系数都应如实呈现。`NOT_READY` 表示执行证据不足，不生成虚假净值，也不可发布该回测；独立预测结果仍可审核发布。

## 使用顺序

1. 使用管理员账户登录，打开 `/#/models`（模型工作台）。普通用户只能访问 `/#/user` 的“已发布模型结果”，不能提交计算。
2. 选择“训练模型”，确认已入库数据的真实起止日期，选择算法和受限参数后提交。界面“已入队”不等于已训练，关闭页面不停止任务。
3. 完成后刷新“已保存模型”，查看特征数、参数、实际训练轮数、设备与模型已见数据边界。用指定版本提交预测或回测，不能替换它的股票池、scaler 或特征顺序。
4. 在结果列表预览排行、单股记录、权益分页、交易、未成交说明和成本假设。预测仅有模型分数，不生成虚拟收益曲线。历史研究与模型选择后的样本分别标注，后者也不等于保证有效的前瞻检验。
5. 管理员明确点击“确认发布给用户”。发布的是 `result_id`，不是自动发布模型。重新训练/计算产生新版本，旧模型和旧封存包不覆盖。撤回后用户端后续请求返回不可见；已经下载到浏览器的数据无法追溯收回。

保留的 `/#/training` 是旧工程验证入口，不会替代新工作流；两者共享同一持久化队列与执行槽。旧 Transformer signals 端点与历史封存结果保持不变。

## 算法与单位

| 算法 | 训练范围与参数 | 输出 / 执行语义 |
| --- | --- | --- |
| XGBoost | 单只沪深股票，1–100棵树、深度1–6 | 下一交易日上涨概率；概率≥0.5才作为研究买入信号，持有1交易日 |
| LightGBM | 固定沪深A股全市场；原收益或日截面排名，1–300轮，RMSE/RankIC早停，训练期探针 | 原收益或中心化排名分数，二者不能混称概率；持有5交易日 |
| Transformer | 显式冻结10–20只股票，默认20只、1 epoch、batch1，上限5 epoch/batch2，保留探针 | 复用原特征/scaler/股票映射；标签 `open_T5/open_T1-1`，标签偏移5但实际持有4交易日 |

Transformer 原始数据集要求至少10只股票，此入口不是全市场GPU训练入口。GPU可用时使用CUDA并限制单进程显存预算（至多约4GiB且不超过设备80%），记录实际设备与峰值。探针和部分预处理仍使用CPU。A100大规模配置需后续明确扩容。

默认20只建议池仅按可信源清单的完整起止日期和交易日行数筛选，不按收益挑选；显式提交后池不再改变。当前 Transformer 小池输入沿用严格会话覆盖要求，有已知停牌/退市缺口不代表行情造假，但该配置可能被拒绝，不能自动填充或静默移除成员。LightGBM 的固定全市场管线则按上市、停牌等证据逐日统计样本资格，不要求补成全股票×全交易日网格。

三者都保存原生模型并重加载验证；HTTP 请求不加载 torch，不推理，不回测。推理读取未成熟标签的最新特征，使用固定训练 scaler；保留完整原始依赖历史计算路径依赖指标。数据库后续扩展时，新模型核对冻结历史前缀；历史价格或算法依赖改变需明确重训，不自动改写模型。

LightGBM 使用磁盘矩阵；全市场数据逐股读取。预测逐股/小池执行，只有回测账本汇总所需分数与价格。默认全市场资源预检：可用内存≥8GiB、临时盘≥16GiB；子进程树≤6GiB、最多60分钟、盘剩余≥2GiB。不足时明确失败，不静默缩池。行情稀疏由上市日期、已证实停牌等解释，不能补造价格。

本机账本尚非无限长的流式账本：提交回测前，以固定股票数×已完成交易日数（含执行尾部）预检，最多250,000个价格观测；运行时再次逐股累计限制，且只保留区间内7列价格，不保留特征或历史前缀。全市场需选较短区间，长达多年的全市场回测明确拒绝，不等到OOM后缩池重试。扩容/流式账本是后续工作。

服务器以可信交易日历和上海时间判定已完成交易日：严格16:00之后才可使用当日，否则前一已完成交易日；休市顺延。UI来源上限与后台信号/执行尾部均受此限制，不以库中已出现某日记录作为收盘证据。

## 首次部署与迁移

在项目根的 `StockAnalysisSystem/.env` 配置现有数据库、protected登录、可信来源/日历；密钥用真实值，不写 `${VAR}` 嵌套占位符。以下命令工作目录为 `StockAnalysisSystem/python-services/stock-analysis-app`，Python使用本机已配置环境：

```text
python scripts/training_worker.py status
python scripts/training_worker.py migrate-workflows
python -m uvicorn api.forecast:app --host 127.0.0.1 --port 8084 --no-proxy-headers
python scripts/training_worker.py run
```

迁移前暂停新提交、确认执行槽空闲并停止旧worker，再显式执行 `migrate-workflows`，重启新版本API及唯一worker。不要让两台机器或两个不同代码版本同时跑worker。正常取消需等待worker确认进程退出；隔离槽不得强制清空，需要原机器核查与受控恢复。API启动不自动改表。

该命令只新增三张索引/审计表：`analysis_workflow_model`、`analysis_workflow_result`、`analysis_workflow_publication`，不删除行情、特征或旧任务。旧任务表与鉴权表必须已完成各自显式迁移。

`TRAINING_ARTIFACT_ROOT` 默认 `StockAnalysisSystem/.runtime/training_tasks/candidates`；`TRAINING_CALENDAR_FILE` 默认 `.runtime/training_tasks/calendar.json`。相对路径均相对系统根目录解析，不依赖开发者盘符。磁盘矩阵在产物根相邻的 `workflow_work` 中，是可再生成的训练输入，不属于模型封存清单。API和worker必须共享配置、代码、数据库、日历与产物盘，并给独立输出预留空间；不可覆盖历史冻结模型。

远程访问沿用现有HTTPS反向代理、同源会话和CSRF，数据库/API内部端口不直接公开。Vite开发代理把新管理接口及published-results只读接口发往8084；发布读取不走mock。未运行worker时任务停留QUEUED，不伪造成功。

## API与版本

- `GET /api/admin/workflows/capabilities`：来源边界、默认Transformer池、资源和槽状态。
- `GET/POST /api/admin/workflows/jobs`：共享队列；POST必须带 `Idempotency-Key` 和CSRF，`kind=model_workflow`，操作 `train/predict/backtest/import`。详情/事件/取消复用 `/api/admin/training/jobs/{id}`。
- `GET /api/admin/workflows/models`：已保存模型；`GET /api/admin/workflows/results` 与 `/{id}`：管理员草稿预览。
- `POST /api/admin/workflows/results/{id}/publish` 或 `/withdraw`：当前管理员审核，记录操作者、时间和版本；无收益阈值。
- `GET /api/analysis/published-results` 与 `/{id}`：登录用户只读已发布版本；草稿或撤回版本404。

列表 `limit` ≤20、`before` 为服务端返回游标；明细 `section=scores/equity/trades/orders/writeoffs/requirements`，`limit` ≤100、`after` 为服务端返回的行游标，可选沪深 `ts_code`。scores读取worker已保存的最新日排行，不在请求时重算；所有分区先核对完整封存哈希。页面曲线仅代表当前分页，不隐藏未加载区间。接口响应不包含服务器路径和原始异常。

导入只接收已成功旧工程任务的32位ID，或已封存验证实验ID加固定trial：`raw_rmse20/raw_rmse300/raw_rankic300/csrank_rankic300`（LightGBM）。旧研究分支无需qualified为真；必须通过实际父候选/探针/模型绑定校验。禁止传文件路径、上传pickle或任意Python配置。旧导入模型无历史前缀证明时仍绑定原始来源，数据扩展需重训。

## 运行与交付边界

代码回归中的小样原生训练不等于已在正式数据库执行全市场训练，也不等于真实管理员已经发布结果。上线验收应由正常管理员登录提交小任务，逐个确认训练、重加载、预测、回测完成或缺口说明、预览、显式发布与撤回；不能伪造生产会话。

新训练数据、测试、规划、模型、缓存、日志和密钥不入GitHub；仅提交业务实现与本文档。正式结果与模型需要单独的持久化磁盘备份。
