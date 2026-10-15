# 项目记忆

## 快速启动（已在 2026-10-13 验证可用）

一键启动整个系统（Docker 栈 + 全部 Java/Python/前端服务 + Flink 作业提交）。
**必须用 WMI 方式启动主脚本**，否则进程会随 Claude 会话结束被 Job Object 杀掉：

```powershell
$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
  CommandLine    = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem\scripts\runtime\start_all_services.ps1"'
  CurrentDirectory = 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem'
}
"PID=$($r.ProcessId) ReturnValue=$($r.ReturnValue)"   # ReturnValue=0 即成功拉起
```

- 全程耗时数分钟（Docker Desktop 最长等 240s，各服务端口各等 90s）。
- 进度/完成标志：`StockAnalysisSystem\logs\runtime\start_all.log` 出现 `=== start_all done ===`；各服务日志在同目录 `*.log`。
- 脚本是**幂等**的：端口已 UP 的服务会跳过，重启单个服务直接重跑即可。

### 启动后验证清单

```powershell
foreach ($p in 3306,8123,9092,8080,8081,8082,8083,8084,8501,5173) {
  try { (New-Object Net.Sockets.TcpClient('127.0.0.1',$p)).Close(); "$p UP" } catch { "$p DOWN" }
}
Invoke-RestMethod http://127.0.0.1:8083/actuator/health          # {"status":"UP"}
Invoke-RestMethod http://127.0.0.1:8080/actuator/health
Invoke-RestMethod 'http://127.0.0.1:8083/api/analysis/indicators/000001.SZ?limit=3'  # 200+数据
docker exec stock-flink-jobmanager flink list                    # stock-daily-indicator-v1 RUNNING
```

| 端口 | 服务 |
|---|---|
| 3306 | MySQL（外部安装，非容器） |
| 8123 | ClickHouse（容器 stock-clickhouse） |
| 9092 | Kafka（容器 stock-kafka） |
| 8080 | kafka-consumer-service |
| 8081/8082 | Flink JobManager / TaskManager |
| 8083 | market-api-service |
| 8084 | FastAPI（stock-analysis-app V0.7） |
| 8501 | Streamlit |
| 5173 | Vue 前端（Vite） |

### 已知坑（都踩过，别再踩）

1. **`.env` 值里禁止写 `${VAR}` 占位符**：docker compose 和 PowerShell 按字面量传递，但 Spring 的 `getProperty()` 会解析嵌套占位符 → 应用发出的密码和服务端不一致 → ClickHouse 516 → indicators 503。这是 2026-10-13 排查半天的根因。
2. **主脚本里 flink submit 那行有 `2>&1` bug**：SLF4J 警告会被内层 `$ErrorActionPreference='Stop'` 升级为终止错误，日志里 flink submit 显示为空/失败但通常无害（作业已在 RUNNING 时脚本本就拒绝重复提交）。若验证发现作业没跑起来，手动在**同一会话**里补交（⚠️ 外层不要加 `2>&1`）：
   ```powershell
   $sys = 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem'
   Get-Content "$sys\.env" | Where-Object { $_ -match '^[A-Za-z_]+=' } | ForEach-Object { $k,$v = $_ -split '=',2; Set-Item "env:$($k.Trim())" $v.Trim() }
   $env:JAVA_HOME = 'F:\Java\jdk-21'; $env:FLINK_JAVA_HOME = 'F:\Java\jdk-21'
   $env:PATH = "F:\Java\jdk-21\bin;$env:PATH"
   & "$sys\scripts\submit_v05_flink_job.ps1"
   ```
3. **PS 5.1**：外层 `2>&1` + 内层 EAP=Stop 会把原生命令的 stderr 升级为终止错误；`&&`/`||` 不可用；写文件用 `Out-File -Encoding utf8`。
4. **ClickHouse 认证排障**：认证失败不会进 query_log（别以此判断"请求没到"）；看响应头 `X-ClickHouse-Exception-Code`（516=AUTHENTICATION_FAILED）。Spring 侧用 `--management.endpoints.web.exposure.include=env --management.endpoint.env.show-values=ALWAYS` 起临时实例查 `/actuator/env/<KEY>`，能直接看到解析值和属性源。
5. `start_all_services.ps1` **尚未提交 git**（untracked），丢了就没了。
6. 用户的 IDE 会在后台重建 jar / 跑全盘 git 扫描——发现 jar mtime 变化或 PID 被复用（如 java 进程 PID 变成 git.exe）先想到这个，不是灵异事件。

### 环境事实

- JDK：`F:\Java\jdk-21`（market-api 为 Spring Boot 4.1.0 fat jar，`java -jar` 启动，工作目录 `java-services\market-api-service`）
- 主 `.env`：`StockAnalysisSystem\.env`（compose 用 `--env-file` 引它）
- compose 文件：`StockAnalysisSystem\infrastructure\docker-compose.yml`
- 展示密钥类值时一律打码（只报长度/是否相等），这是本会话的既定安全习惯。

## 前端架构约定（2026-10-14 用户拍板；2026-10-15 客户端只读信号已落地）

前端要拆成**两种页面/两套入口**：

| | 客户端（对外） | 后台管理（我们自己用） |
|---|---|---|
| 能力 | **只能看结果，不能训练** | 可训练、可管理（配置/触发训练/发布结果） |
| 典型页面 | 预测结果、排行、指标、图表等只读视图 | Transformer 训练面板、参数配置、数据采集/消费监控、模型管理 |

- **红线：客户端绝不暴露任何"训练/触发计算"的能力**，只消费后台产出的结果。
- 对照现状（都在 `StockAnalysisSystem\frontend\`，单一 Vue 应用，5 路由 `/overview /collector /monitor /analysis /user`，**无鉴权/无角色**）：
  - `views/analysis/AnalysisTransformer.vue` 的「运行训练 + 预测」按钮 = 典型**后台专属**能力，客户端不可见。
  - 目前该按钮走的是 `mock/analysis.js` 种子随机（client.js 里 transformer train/predict/configs 路径永远命中 mock，不发后端请求），**不是真训练**；真训练/回测在 Streamlit(`dashboard.py`) + `models/transformer/*.parquet`。
- **待定（实现前需用户确认）**：① 单应用按角色路由 vs 两个独立应用/构建（现状：单应用，`/user` 用 `meta.userApp` 隔离布局）；② 鉴权方案（当前前端零鉴权，后端 market-api 也未见认证）。

### 已落地（2026-10-15，本会话实现，未提交 git）

- **客户端第 4 tab「Transformer 信号」**（`views/analysis/AnalysisTransformerSignals.vue`）：冻结 6 模型封存信号包的**只读呈现**——状态横幅（LATE_DIAGNOSTIC 诚实警示）、元信息、所选股面板（在/不在 20 只试点范围两种态）、6 模型 Top5、共识排行+条形图、sha256 完整性展示。数据契约（原待定③对信号已解决）：读 8084 新端点。
- **后端 `GET /api/analysis/transformer/signals[?date=YYYY-MM-DD]`**（`api/transformer_signals.py`，嫁接进 `api.forecast:create_app`，`uvicorn api.forecast:app` 自动生效）：每次响应前 `ForwardSignalStore.verify()` 防篡改复核 + payload sha 对照 marker；mtime 键控内存缓存（上限 32）；fail-closed：坏参数 400 / 无包 404 / 任何异常净化 503；**请求路径永不加载 torch**（子进程测试断言）。惰性导入 pandas/sklearn 链 → 首请求约 3s，缓存后 ~10ms。
- `frontend/.env` 默认 `VITE_DATA_SOURCE=hybrid`（被 gitignore 裸 `.env` 规则忽略；**localStorage `sas-data-source` 优先级更高**，旧浏览器存过 mock 的要手动 removeItem）。client.js 白名单精确相等匹配 `signals` 路径，train/predict/configs 到不了网络。
- 顺带修复：`AnalysisRisk.vue` hybrid 空白表（live 行是发布摘要结构，列/rowKey 按数据源切换 + loading/error/guard）。
- 验证快照：后端 pytest 672 passed（含新 `test_transformer_signals_api.py` 26 例）；前端 `npm test` 71 passed + build 成功；API 返回的 marker/receipt/payload/binding sha256 与磁盘 `Get-FileHash` 实测一致；5173 代理同 body。
- ⚠️ 现网唯一信号包是 **2026-10-08（LATE_DIAGNOSTIC，持有窗 10-09→10-15 已结束）**的历史封存快照；出新信号须人工重跑 `scripts/run_transformer_forward_signals.py`（约 1h GPU + Tushare 配额），API 发现逻辑对未来新包自动取 latest，前端绝不触发。
