# 网页采集管理

`/#/collector` 现在是管理员的真实缺口补采入口，不再模拟 CLI 成功。普通用户不可调用采集 API；管理员写操作要求正常登录会话、同源 Origin 和 CSRF。仅受保护模式提供此能力，不提供匿名本机写入旁路。

## 操作顺序

1. 使用已配置的 MySQL、采集源凭据与可迁移运行目录。分析 API 和 collector 必须连接同一数据库，并使用相同 `PREPARATION_DATA_ROOT`、`RUNTIME_PATH_MAP_FILE` 配置。默认运行目录为项目 `StockAnalysisSystem/.runtime/stock_pool_preparation`，不是某台电脑的固定绝对路径。
2. 在 `python-services/python-collector` 执行一次 `python scripts/prepare_stock_pool_data.py web-migrate`。只创建 `market_collection_request` 和 `market_collection_request_day`，不删除或改写行情，不启动采集。等价 SQL 为 `infrastructure/mysql/migrations/V0.9_007_collection_web.sql`。原采集队列及行情表应已按既有迁移准备好；本命令不代替它们。
3. 重启使用当前代码的 FastAPI，沿用 `python -m uvicorn api.forecast:app --host 127.0.0.1 --port 8084 --no-proxy-headers`。Vite 与 HTTPS 网关已有 `/api/admin/` 代理，无需开放额外服务端口。
4. **确认旧 worker 已停止、其他电脑不在采集**后，在 collector 目录运行唯一独立 worker：`python scripts/prepare_stock_pool_data.py worker --nightly --nightly-at 20:00`。沿用已有进程监管；Windows后台持久启动遵循项目启动说明，不要直接从会话启动后假设进程永久存活。不要开启第二个 worker，也不要删除锁文件来绕过占用。已有暂停决定必须由管理员明确解除，网页不会自动启动或恢复进程。
5. 管理员进入“数据采集”，选择2023-01-01起的日期范围并提交。上海时间严格大于16:00可提交到当天，否则只能到前一天；真实交易日由 worker 的交易日历确定。范围内固定沪深 A 股，不含北交所。提交只持久化请求，不在 HTTP 中请求 Tushare。
6. 查看“网页提交记录”和“真实交易日任务”。网页请求 `SUBMITTED / 已生成日任务` **不是行情采集完成**。只有对应日任务最终状态与实际入库结果才能说明数据就绪。失败或部分完成可逐条重试；配额等待保留原等待截止时间，不提供绕过限流按钮。

## 状态与恢复

- 网页请求：`QUEUED → PLANNING → SUBMITTED`；请求规划遇到配额/网络错误进入 `WAITING_RETRY`，其他失败显示 `FAILED`。规划失败可核查原因后重新提交日期范围；已有数据仍会被跳过。
- 手工补采不等待夜间20:00。原日任务计划按分钟记时，最早16:01；若恰在16:00之后、16:01之前提交当天范围，请求会持久等待至16:01，不标作失败，也不绕过已收盘日期限制。
- 日任务继续使用原 `market_update_run` 缺口队列：`QUEUED / RUNNING / WAITING_RETRY / SUCCEEDED / PARTIAL / FAILED`。历史旧版非缺口任务不接入新重试入口。
- 请求幂等键按管理员和内容绑定。网络超时后同一页面重试沿用原键，不能把超时当失败后自动复制任务。刷新页面丢失内存中的意图时，先核对提交记录再决定是否重提。
- worker 在原有独占根锁内规划，先把不可变缺口批次绑定到网页请求，再创建并关联日任务。中断后复用原批次和原任务身份，不按已变化的数据重新规划半个请求。
- worker 保留原限流、请求来源证据、断点、上市/退市有效期及合法停牌处理。不全量核对旧价格，不补造价格，不触发模型训练，也不自动发布预测。每日基本指标沿用原缺口管线，不切换传统 CLI 的 mysql/kafka/dual 输出配置。
- 页面的 worker 状态只验证本机进程身份，**不证明该进程已启用 `--nightly`**；异机或权限不足显示未知。夜间计划展示已保存调度状态，不擅自清除错误或重置时间。
- 页面每15秒刷新可见页面状态；数据库/API故障明确报错，不回退演示数据。预览仅支持 `stock_basic`、`stock_daily`、`stock_daily_basic`，每次最多50行，页面默认20行；日线及基本指标先用现有日期索引定位最新存储日期，再展示该日期的沪深A股记录，不在全部历史上排序。不允许浏览任意数据库表或执行命令。

## API

全部位于 `/api/admin/collector`：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/capabilities`、`/summary` | 日期上限、运行状态、真实队列计数 |
| GET | `/requests?limit=20&offset=0` | 网页提交记录 |
| POST | `/requests` | `{ "start": "2026-10-01", "end": "2026-10-13" }`；返回202表示入队 |
| GET | `/tasks?limit=20&offset=0&request_id=<id>` | 可按网页请求过滤日任务；不传request_id查看全部缺口任务 |
| GET | `/tasks/<id>` | 单日状态、阶段、可公开数值计数 |
| POST | `/tasks/<id>/retry` | 空JSON对象，单任务重试入队 |
| GET | `/preview/<table>?limit=20` | 固定白名单表的真实预览 |

POST 需要 `Idempotency-Key`（32位小写十六进制）和已有认证头。服务端限制参数、分页和请求体大小；HTTP响应不包含运行目录、来源原文、数据库错误、凭据或worker所有权令牌。缺迁移/数据库故障返回503，而不是空列表或模拟成功。

此阶段不提供指数/行业股票池选择、不自动补齐因子/交易执行证据、不保证补采完成即可满足所有模型训练或回测的数据要求。它只完成现有全市场行情与每日基本指标采集的网页控制闭环。
