# V0.4 README Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the GitLab boilerplate homepage and bring both repository README files up to date with the implemented V0.4 architecture and operating workflow.

**Architecture:** The root README is the concise project landing page. `StockAnalysisSystem/README.md` is the operational guide, while protocol, migration, acceptance, and code-reading detail remains linked from existing focused documents.

**Tech Stack:** Markdown, PowerShell command examples, Docker Compose, Python 3.12, Java 21, Maven, MySQL 8, Kafka, Streamlit.

## Global Constraints

- `python-collector` is the only service allowed to call Tushare or AkShare SDKs.
- `stock-analysis-app` reads MySQL and compatible local caches; it does not fetch external market data.
- MySQL output remains independently usable when Kafka is unavailable.
- Do not include secrets, personal absolute paths, or claims that an external provider is always available.
- Contributions must be pushed to the personal Fork and submitted to the team repository through a Merge Request.

---

### Task 1: Replace the repository landing page

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: current V0.4 service layout and commands under `StockAnalysisSystem/`
- Produces: GitLab repository landing page with links into `StockAnalysisSystem/docs/`

- [ ] **Step 1: Replace the GitLab template**

Write a Chinese project homepage containing these exact sections, in order:

```markdown
# StockAnalysisSystem

基于 Python、Kafka、Java、MySQL 的股票数据采集与分析系统。

## V0.4 当前架构
## 核心能力
## 项目目录
## 核心数据表
## 快速启动
## 页面与接口
## 测试
## 文档导航
## 贡献流程
## 风险提示
```

The architecture block must show:

```text
Tushare / AkShare
       |
       v
Python Collector ----> MySQL ----> Stock Analysis App / Streamlit
       |
       v
     Kafka ----> Java Consumer ----> DLT / Monitoring API
```

Use repository-relative links beginning with `StockAnalysisSystem/`. State that V0.4 adds `stock_daily_basic` and `stock_constituent`, centralizes external SDK access, and preserves the V0.3 Kafka rollback path.

- [ ] **Step 2: Verify landing-page references**

Run:

```powershell
Select-String -Path README.md -Pattern 'Getting started|Editing this README|V0.3 当前架构'
```

Expected: no matches.

Run:

```powershell
Select-String -Path README.md -Pattern 'V0.4 当前架构|python-collector|stock_daily_basic|stock_constituent|Merge Request'
```

Expected: all five concepts are present.

---

### Task 2: Upgrade the operational README

**Files:**
- Modify: `StockAnalysisSystem/README.md`

**Interfaces:**
- Consumes: CLI commands from `python-services/python-collector/app/main.py`, Docker services from `infrastructure/docker-compose.yml`, and current Java Maven modules
- Produces: developer-facing V0.4 operating guide

- [ ] **Step 1: Upgrade the title and data-flow explanation**

Use the title `# StockAnalysisSystem V0.4`. Explain the four boundaries:

```text
python-collector     外部数据采集、校验、MySQL/Kafka 输出
kafka-consumer      Kafka 消费、协议校验、DLT、监控
stock-analysis-app  MySQL/兼容缓存读取、特征、训练、预测、可视化
infrastructure      MySQL/Kafka/Kafka UI 本地基础设施
```

- [ ] **Step 2: Document tables and commands**

Document these tables with their current responsibilities:

```text
stock_basic         股票基础资料
stock_daily         原始日线 OHLCV
stock_daily_basic   原始换手率、估值、总市值
stock_constituent   指数/行业成分股快照
stock_features      分析端生成的传统特征
analysis_result     分析与预测结果
collection_task     采集幂等、状态和重试记录
```

Include these collector examples:

```powershell
python -m app.main daily-market --date 20260817
python -m app.main daily-basic-history --start 20220104 --end 20260817
python -m app.main constituent --type index --code 000300.SH --date 20260817
python -m app.main retry-failed
```

Explain `COLLECTOR_OUTPUT_MODE=mysql|kafka|dual`, with `mysql` as the rollback-safe default.

- [ ] **Step 3: Add test and document links**

Include collector, analysis, and Java test commands. Link to:

```text
docs/V0.3_MESSAGE_SCHEMA.md
docs/V0.3_TEST_CASES.md
docs/V0.3_RECONCILIATION_REPORT.md
docs/V0.3_RELEASE_NOTES.md
docs/V0.4_RELEASE_NOTES.md
docs/V0.4_MARKET_DATA_MIGRATION_REPORT.md
python-services/stock-analysis-app/CODE_READING_GUIDE.md
```

- [ ] **Step 4: Verify files, links, formatting, and commit**

Run a PowerShell check that extracts local Markdown link targets from both README files and verifies each path with `Test-Path`. Expected: zero missing targets.

Run:

```powershell
git diff --check
git status --short
```

Expected: only the two README files and this plan are changed before commit.

Commit:

```powershell
git add README.md StockAnalysisSystem/README.md docs/superpowers/plans/2026-08-19-readme-refresh.md
git commit -m "docs: refresh v0.4 project readme"
```

Push only to the personal Fork branch `origin/codex/unify-market-data-ingestion`; the existing team MR !8 updates automatically.

