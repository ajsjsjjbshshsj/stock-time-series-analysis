# GitHub 迁移与本地项目整理设计

## 目标

将当前最新 StockAnalysisSystem 以完整 Git 历史迁移到公开 GitHub 仓库
`ajsjsjjbshshsj/stock-time-series-analysis`，停止使用 GitLab 作为开发远程，并将本地项目整理为一个干净、独立的 GitHub 克隆。

## 最新版本判定

迁移不直接信任 GitLab `main`，而以本地提交关系和工作区状态为依据。

审计结果：

- 最新候选是 `codex/flink-realtime-pipeline`，提交为
  `fd229e0d0a7bbb0b5ea48a960d1d3983c67d845d`，工作区干净。
- 旧独立仓库提交 `788b868`、主检出目录提交 `9c15887`、decimal worktree
  提交 `722f7b1` 均是 `fd229e0` 的祖先，已包含在最新历史中。
- 旧主检出目录的未跟踪 `Java/` 是 JDK 21 二进制安装目录，不是项目源码。
- 旧 `.idea/` 是 IDE 本地配置，不进入新仓库。
- 本地运行配置使用
  `F:/java-stock-analysis/kafka-stock-project/StockAnalysisSystem/.env`；该文件只复制到新克隆，继续被 Git 忽略，不上传到公开仓库。

迁移前会在当前最新分支增加本设计和实施计划，因此 GitHub 初始 `main` 会指向
包含迁移文档的新提交，而不是停留在 `fd229e0`。

## GitHub 目标

- 所有者：`ajsjsjjbshshsj`
- 仓库名：`stock-time-series-analysis`
- 可见性：Public
- 描述：`对股票时间序列的分析、预测、建模及可视化`
- 默认分支：`main`
- URL：`https://github.com/ajsjsjjbshshsj/stock-time-series-analysis`

账号属于个人账户，因此协作成员通过 GitHub Collaborators 邀请；若未来需要 GitHub
Teams，再将仓库转移到 GitHub Organization。

## 迁移策略

1. 以当前干净 worktree 的完整对象历史作为迁移源。
2. 将当前最新提交推送为 GitHub `main`，并保留正在使用的
   `codex/flink-realtime-pipeline` 分支。
3. 推送 Git 标签；不迁移已经合并且不再需要的旧功能分支，避免新仓库继续堆积历史工作分支。
4. 在新路径 `F:/java-stock-analysis/stock-time-series-analysis` 从 GitHub 重新克隆。
5. 将最新本地 `.env` 复制到新克隆的 `StockAnalysisSystem/.env`，并确认 Git 不跟踪它。
6. 比较源与新克隆的提交 SHA、Git tree SHA、分支和关键文件。
7. 在新克隆运行 Collector、Analysis 和 Java 基线测试。
8. 验证通过后，才移除旧 worktree、旧主仓库和旧项目目录。

## 远程策略

新克隆只保留：

```text
origin  https://github.com/ajsjsjjbshshsj/stock-time-series-analysis.git
```

不在新克隆中配置 GitLab `origin` 或 `upstream`。GitLab 远程仓库不主动删除，以免破坏
历史审计；停止向其推送即视为弃用。

## 本地最终状态

最终保留：

```text
F:/java-stock-analysis/stock-time-series-analysis
```

验证通过后清理以下旧项目范围：

```text
F:/java-stock-analysis/kafka-stock-project
F:/java-stock-analysis/kafka-consumer-service/StockAnalysisSystem
F:/java-stock-analysis/kafka-consumer-service/worktrees/stock-daily-decimal-migration
F:/java-stock-analysis/kafka-consumer-service/worktrees/unify-market-data-ingestion
```

`F:/java-stock-analysis/kafka-consumer-service` 根目录还包含 `.venv`、IDE 配置和旧 Compose
文件。只有在确认其中不存在项目外个人文件后，才整体删除该根目录；否则只删除上面明确列出的项目目录。

## 安全与验证

- 创建 GitHub 仓库前确认同名仓库不存在。
- 公开仓库推送前扫描已跟踪文件，确认没有 `.env`、Token、密码或大体积二进制文件。
- 不使用 `git push --mirror`，避免把 GitLab 远程跟踪引用和无用旧分支复制到 GitHub。
- 删除前解析并逐个打印绝对路径，确认目标均位于上述明确范围。
- 新克隆、远程 SHA、tree SHA、测试和 `.env` 忽略状态任一失败时停止清理。
- 不删除 GitLab 远程仓库；如以后需要删除，单独授权执行。

