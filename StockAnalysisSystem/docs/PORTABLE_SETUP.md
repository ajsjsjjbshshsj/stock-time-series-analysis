# 首次安装与完整副本恢复

源码可放在任意目录，包括中文或空格目录。命令中的 `python` 指已激活环境的解释器，不依赖原开发者的磁盘路径。以下命令从 `StockAnalysisSystem` 目录执行，每条单行。Windows 为实际验收平台；Linux、CUDA、服务器鉴权与高可用需要另行部署验收。

## 首次安装

1. 克隆仓库；Python 3.12、MySQL 8 是采集层基础环境。完整界面另需 Node/npm、JDK 21、Maven、Docker Desktop/Compose。MySQL-only 采集不要求 Kafka/Flink/ClickHouse。
2. 创建并激活虚拟环境，然后安装所用服务依赖。分析训练依赖 TA-Lib、TensorFlow、LightGBM；Transformer 还需按机器安装 PyTorch。CUDA 是可选训练能力，启动/API 不自动安装或训练。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r python-services/python-collector/requirements.txt
python -m pip install -r python-services/stock-analysis-app/requirements.txt
Copy-Item .env.example .env
```

只使用采集层时跳过分析依赖安装。编辑 `.env` 填写自己的数据库配置和 Tushare Token，完整链路还需独立的 ClickHouse 密码。环境变量优先于配置文件；不要将 `.env`、数据库备份或模型上传 GitHub。旧服务目录若有自己的 `.env`，可能优先于共享 `.env`，应统一维护，避免不一致。

3. 在 MySQL 客户端显式建立目标数据库并授权（数据库名应与 `DB_NAME` 相同）：

```sql
CREATE DATABASE stock_analysis CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
```

4. 二选一：新用户初始化**已有空库**，或在停止写入、确认版本与完整备份后用 MySQL 工具恢复完整数据库。

```powershell
python scripts/manage_project.py init-db --empty-only
python scripts/manage_project.py doctor --profile collector
```

`init-db` 在首条 DDL 前检查所有表/视图，非空库拒绝操作。它使用基础 ORM schema 的副本，日线价格为 DECIMAL(20,6)，量/金额为 DECIMAL(24,4)，保留唯一键；再应用 V0.9 两个新增元数据迁移。不会执行 V0.3 旧表交换/删除脚本，不填入行情/模型。MySQL DDL 不能保证事务回滚；失败留下部分表时先诊断，不在已有库盲目重试或清库。

已有库**不能**用 `init-db` 升级。先备份，再按对应版本指南显式执行所需迁移；V0.9 两个新增表脚本可检查后手动应用。旧 Float 日线升级按 V0.3 指南验收，不自动删除旧表。

`doctor` 只读检查配置、依赖及数据库表/日线精度和唯一键；错误不打印凭据，不建表、不写日志、不排队、不初始化 SDK。成功仅表示先决条件通过，不保证已拥有行情或可用模型。`full` 另检查工具、ClickHouse/Kafka 端口可达性；端口通不等于认证、Flink 作业或完整链路验收成功。

## 显式启动服务

完整基础设施按 [V0.6 指南](V0.6_CLICKHOUSE_MARKET_API.md) 配置。Compose 需提供已填写的共享环境文件，ClickHouse 密码不能为空；不删除现有卷或 checkpoint。

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml up -d
python scripts/manage_project.py doctor --profile full
```

首次构建 Java 公共模块（从 `java-services` 目录运行）：

```powershell
mvn -pl common-model -am install -DskipTests
```

前端先从 `frontend` 目录执行 `npm ci`，复制 `.env.example` 为 `.env.local`，将 `VITE_DATA_SOURCE` 改为 `hybrid` 以请求真实 API。默认 `mock` 是演示；目标地址分别为 Consumer 8080、Market 8083、Prediction 8084，远端部署按实际地址修改。确认接口不可用提示，不能把演示页面当作真实验收。

回到 `StockAnalysisSystem`，按需要在独立终端启动，Ctrl+C 停止：

```powershell
python scripts/manage_project.py start --service prediction
python scripts/manage_project.py start --service market
python scripts/manage_project.py start --service consumer
python scripts/manage_project.py start --service frontend
```

启动器使用当前 Python/PATH，切换到所选服务目录。Market 启动默认关闭指标消费监听，Consumer 启动会消费已有 Kafka 消息；二者都不调用行情 SDK。ClickHouse 指标消费及 Flink 作业按版本指南单独启用。没有行情、已发布模型或结果时预测接口返回资源缺失/依赖不可用，不在 GET 或启动时训练、采集、发布假预测。

## 数据根和完整恢复

代码、数据库、模型和运行来源是不同交付物。完整恢复需要相同 Git commit、完整数据库、运行数据根（来源/断点/回执/共享限频状态）及用到的模型；不能只拷三张行情表就声称项目一致。凭据由接收者配置，不随备份公开交接。

`PREPARATION_DATA_ROOT` 留空默认使用项目的 `.runtime/stock_pool_preparation`；相对值相对于 `StockAnalysisSystem`，不是当前终端目录。绝对值是部署配置，允许自行填写。模型沿用所用训练/发布入口的配置，未包含在 Git 仓库中。

新来源引用为 `runtime://gap_batches/...` 或 `project://...`，运行时在当前配置根解析，跨目录无须修改原文件。旧来源 JSON 的绝对路径是哈希证明的一部分，**不要搜索替换、重新签封或清空来源索引**。

旧副本设置 `RUNTIME_PATH_MAP_FILE=runtime-path-map.json`（相对项目），自行建立未提交的文件：

```json
{
  "schema_version": 1,
  "legacy_runtime_roots": ["C:/previous-project/StockAnalysisSystem/.runtime/stock_pool_preparation"],
  "legacy_project_roots": ["C:/previous-project/StockAnalysisSystem"]
}
```

这里填写副本**原机器**的实际根；新根来自当前位置和 `PREPARATION_DATA_ROOT`。映射仅读取时解释后缀，原 JSON/Parquet/查询身份及 SHA256 不改；未声明旧根、缺失文件、错误哈希、目录逃逸均拒绝，不联网补救。映射文件不会自动生成，也不保证未拷文件能恢复。

恢复前安全停止两端写入，核对来源/模型/备份哈希和 SQL 任务状态。旧 owner/PID/锁不代表新机器的可运行身份；未知或活跃 owner 不自动抢占，不能删除锁或篡改任务身份绕过检查。首先只读诊断并保持暂停，确认后才显式运行现有采集入口。

## 采集与夜间调度独立

上述安装/doctor/start **不会**开启夜间任务或历史补采。需要时按 [夜间更新指南](../python-services/python-collector/README_nightly_market_updates.md) 显式启动统一 worker；每晚 20:00 仅在该进程持续运行时调度。已有队列也可能被处理，运行前核对任务。

生产缓存、来源索引、回执和断点不是测试废文件，应单独备份；测试/验收/规划、真实 `.env`、本机私有启动脚本以及运行产物不属于本次 GitHub 提交内容。没有依赖这些本地测试文件才能运行的业务入口。
