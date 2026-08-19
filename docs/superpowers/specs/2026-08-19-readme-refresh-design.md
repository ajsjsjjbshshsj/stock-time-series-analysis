# GitLab README 更新设计

## 目标

将团队仓库根目录仍在使用的 GitLab 默认模板替换为 StockAnalysisSystem V0.4 项目首页，并同步更新 `StockAnalysisSystem/README.md`，让新成员可以从仓库首页理解系统边界、启动项目并找到深入文档。

## 文档职责

- 根目录 `README.md`：面向第一次访问仓库的人，说明项目定位、V0.4 架构、核心能力、快速启动、页面入口、测试方式和文档导航。
- `StockAnalysisSystem/README.md`：面向准备运行或开发项目的人，保留更具体的目录职责、数据库表、采集命令、回滚方式和开发流程。
- 详细迁移验收、消息协议与代码阅读材料继续放在 `StockAnalysisSystem/docs/` 和各服务自己的 README 中，避免根 README 膨胀。

## 内容结构

根 README 按以下顺序组织：

1. 项目简介与当前 V0.4 状态。
2. 数据链路架构图。
3. Python Collector、Java Consumer、分析应用和基础设施的职责。
4. 核心数据库表说明。
5. 环境准备和最短启动流程。
6. Kafka UI、Java 监控页与 Streamlit 页面入口。
7. 测试命令和文档导航。
8. Fork、个人分支、Merge Request 的团队贡献流程。

子目录 README 在现有 V0.3 内容上升级为 V0.4，并补充 daily-basic、成分股快照、分析端只读 MySQL、缓存兼容和故障回滚说明。

## 准确性约束

- 不宣称 AkShare 实时接口永远可用；只描述其作为可选数据源。
- 明确 `python-collector` 是唯一外部市场数据 SDK 入口，`stock-analysis-app` 只读 MySQL 和兼容缓存。
- 明确 Kafka 是可回滚消息链路，默认 MySQL 输出仍可独立运行。
- 命令必须与当前 CLI、Docker Compose、Maven 模块和实际目录一致。
- 不在 README 中写入 Token、数据库密码或个人本地绝对路径。

## 验证

- 检查两份 README 不再残留 GitLab 默认模板或错误的 V0.3 项目标题。
- 检查所有相对文档链接在仓库中存在。
- 检查示例命令引用的目录和入口文件存在。
- 运行 `git diff --check`，确认 Markdown 没有空白错误。

