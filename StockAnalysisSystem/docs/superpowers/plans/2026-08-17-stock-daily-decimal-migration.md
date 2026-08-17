# StockDaily DECIMAL Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将约 484 万行的 `stock_daily` 从 `FLOAT` 安全迁移为 `DECIMAL`，完成真实双写验收后按用户要求删除旧表。

**Architecture:** 使用影子表 `stock_daily_decimal` 完成离线复制和逐项校验，通过单条 `RENAME TABLE` 原子交换新旧表。旧表在真实 Collector、Kafka 和 Java Consumer 验收完成前保留为 `stock_daily_float_backup`，所有验收通过后才执行显式删除。

**Tech Stack:** MySQL 8.0.34、InnoDB、PowerShell、Python 3.12、PyMySQL、Tushare、Kafka、Spring Boot Java Consumer。

## Global Constraints

- 只修改 `stock_analysis.stock_daily`，不得修改 `stock_features`、`analysis_result` 或其他业务表。
- 价格和涨跌字段使用 `DECIMAL(20,6)`；`vol`、`amount` 使用 `DECIMAL(24,4)`。
- 不在原表上直接执行字段类型 `ALTER`。
- 不覆盖已存在的 `stock_daily_decimal` 或 `stock_daily_float_backup`。
- 原子交换前必须完成全量行数、主键、业务唯一键、空值和逐行数值校验。
- 删除 `stock_daily_float_backup` 前必须完成真实 MySQL/Kafka/Java 对账。
- 不提交 `.env`、Token、密码、数据库导出文件或运行日志。

---

### Task 1: Add Reproducible Migration SQL

**Files:**
- Create: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_001_prepare_stock_daily_decimal.sql`
- Create: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_002_swap_stock_daily_decimal.sql`
- Create: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_003_drop_stock_daily_float_backup.sql`

**Interfaces:**
- Consumes: existing `stock_analysis.stock_daily` schema.
- Produces: a populated `stock_daily_decimal`, an atomic swap command, and an explicit final cleanup command.

- [ ] **Step 1: Create the prepare SQL**

```sql
USE stock_analysis;

CREATE TABLE stock_daily_decimal (
  id INT NOT NULL AUTO_INCREMENT,
  ts_code VARCHAR(10) COLLATE utf8mb4_general_ci NOT NULL,
  trade_date DATE NOT NULL,
  open DECIMAL(20,6) NULL,
  high DECIMAL(20,6) NULL,
  low DECIMAL(20,6) NULL,
  close DECIMAL(20,6) NULL,
  pre_close DECIMAL(20,6) NULL,
  `change` DECIMAL(20,6) NULL,
  pct_chg DECIMAL(20,6) NULL,
  vol DECIMAL(24,4) NULL,
  amount DECIMAL(24,4) NULL,
  created_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY idx_code_date (ts_code, trade_date),
  KEY idx_trade_date (trade_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

INSERT INTO stock_daily_decimal (
  id, ts_code, trade_date, open, high, low, close, pre_close,
  `change`, pct_chg, vol, amount, created_at
)
SELECT
  id, ts_code, trade_date,
  CAST(open AS DECIMAL(20,6)),
  CAST(high AS DECIMAL(20,6)),
  CAST(low AS DECIMAL(20,6)),
  CAST(close AS DECIMAL(20,6)),
  CAST(pre_close AS DECIMAL(20,6)),
  CAST(`change` AS DECIMAL(20,6)),
  CAST(pct_chg AS DECIMAL(20,6)),
  CAST(vol AS DECIMAL(24,4)),
  CAST(amount AS DECIMAL(24,4)),
  created_at
FROM stock_daily
ORDER BY id;
```

- [ ] **Step 2: Create the atomic swap SQL**

```sql
USE stock_analysis;

RENAME TABLE
  stock_daily TO stock_daily_float_backup,
  stock_daily_decimal TO stock_daily;
```

- [ ] **Step 3: Create the explicit cleanup SQL**

```sql
USE stock_analysis;

DROP TABLE stock_daily_float_backup;
```

- [ ] **Step 4: Check SQL files and commit**

Run:

```powershell
git diff --check
git status --short
```

Expected: only the three migration SQL files are new, with no whitespace errors.

Commit:

```powershell
git add StockAnalysisSystem/infrastructure/mysql/migrations
git commit -m "db: add stock daily decimal migration"
```

### Task 2: Preflight and Populate the Shadow Table

**Files:**
- Use: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_001_prepare_stock_daily_decimal.sql`
- Read: `StockAnalysisSystem/.env` from the original local checkout only; never copy it into the worktree.

**Interfaces:**
- Consumes: local MySQL credentials and `stock_daily`.
- Produces: a fully populated `stock_daily_decimal` without modifying `stock_daily`.

- [ ] **Step 1: Verify preconditions**

Run read-only SQL:

```sql
SELECT VERSION();
SELECT TABLE_NAME
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'stock_analysis'
  AND TABLE_NAME IN ('stock_daily', 'stock_daily_decimal', 'stock_daily_float_backup');
SELECT COUNT(*), MIN(id), MAX(id) FROM stock_analysis.stock_daily;
```

Expected: MySQL 8.0.34; only `stock_daily` exists; count is greater than 4,800,000.

- [ ] **Step 2: Confirm write quiescence**

Run:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Select-Object Id,Path,StartTime
```

Expected: no active Python Collector writing to `stock_daily`. Java Consumer may remain running because it does not write MySQL.

- [ ] **Step 3: Execute the prepare SQL**

Run the MySQL client using `MYSQL_PWD` populated in-process from the ignored `.env`; do not pass the password on the command line.

Expected: `stock_daily_decimal` is created and the insert completes successfully.

- [ ] **Step 4: Record elapsed time and table size**

Run:

```sql
SELECT TABLE_NAME, TABLE_ROWS,
       ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS total_mb
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = 'stock_analysis'
  AND TABLE_NAME IN ('stock_daily', 'stock_daily_decimal');
```

Expected: both tables exist and the shadow table has a comparable size.

### Task 3: Validate the Shadow Table Before Swap

**Files:**
- No file changes.

**Interfaces:**
- Consumes: `stock_daily` and `stock_daily_decimal`.
- Produces: evidence that the two tables are equivalent under the declared casts.

- [ ] **Step 1: Compare counts and primary-key bounds**

```sql
SELECT 'old' AS side, COUNT(*) AS rows_count, MIN(id), MAX(id) FROM stock_daily
UNION ALL
SELECT 'new', COUNT(*), MIN(id), MAX(id) FROM stock_daily_decimal;
```

Expected: both rows have identical counts, minimum IDs, and maximum IDs.

- [ ] **Step 2: Check business-key uniqueness**

```sql
SELECT COUNT(*) AS duplicate_groups
FROM (
  SELECT ts_code, trade_date
  FROM stock_daily_decimal
  GROUP BY ts_code, trade_date
  HAVING COUNT(*) > 1
) AS duplicates;
```

Expected: `duplicate_groups = 0`.

- [ ] **Step 3: Compare every migrated numeric value**

```sql
SELECT
  SUM(NOT (n.open <=> CAST(o.open AS DECIMAL(20,6)))) AS open_diff,
  SUM(NOT (n.high <=> CAST(o.high AS DECIMAL(20,6)))) AS high_diff,
  SUM(NOT (n.low <=> CAST(o.low AS DECIMAL(20,6)))) AS low_diff,
  SUM(NOT (n.close <=> CAST(o.close AS DECIMAL(20,6)))) AS close_diff,
  SUM(NOT (n.pre_close <=> CAST(o.pre_close AS DECIMAL(20,6)))) AS pre_close_diff,
  SUM(NOT (n.`change` <=> CAST(o.`change` AS DECIMAL(20,6)))) AS change_diff,
  SUM(NOT (n.pct_chg <=> CAST(o.pct_chg AS DECIMAL(20,6)))) AS pct_chg_diff,
  SUM(NOT (n.vol <=> CAST(o.vol AS DECIMAL(24,4)))) AS vol_diff,
  SUM(NOT (n.amount <=> CAST(o.amount AS DECIMAL(24,4)))) AS amount_diff
FROM stock_daily o
JOIN stock_daily_decimal n ON n.id = o.id;
```

Expected: all nine difference counts are `0`.

- [ ] **Step 4: Verify target column types and indexes**

Query `information_schema.COLUMNS` and `information_schema.STATISTICS`.

Expected: nine target columns use the declared `DECIMAL` types; primary key, `idx_code_date`, and `idx_trade_date` exist.

### Task 4: Atomically Swap and Verify Application Compatibility

**Files:**
- Use: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_002_swap_stock_daily_decimal.sql`

**Interfaces:**
- Consumes: validated shadow table.
- Produces: new production `stock_daily` and rollback table `stock_daily_float_backup`.

- [ ] **Step 1: Reconfirm no active Collector write**

Check Python processes and MySQL active transactions. Stop if any Collector write is present.

- [ ] **Step 2: Execute the atomic swap SQL**

Expected: one successful `RENAME TABLE`; no intermediate state where `stock_daily` is missing.

- [ ] **Step 3: Verify names, counts, types, and indexes after swap**

Expected: `stock_daily` is the DECIMAL table; `stock_daily_float_backup` is the FLOAT table; counts and key bounds remain identical.

- [ ] **Step 4: Run repository regression tests**

Run:

```powershell
D:\Python\python.exe -m pytest tests/test_stock_repository.py tests/test_mysql_output.py -v
```

Expected: all selected tests pass.

### Task 5: Run Real Dual Acceptance Against the DECIMAL Table

**Files:**
- Modify: `StockAnalysisSystem/docs/V0.3_RECONCILIATION_REPORT.md`
- Modify: `StockAnalysisSystem/docs/V0.3_RELEASE_NOTES.md`

**Interfaces:**
- Consumes: real Tushare daily data, MySQL DECIMAL table, Kafka, and Java Consumer.
- Produces: one traceable real batch and updated acceptance evidence.

- [ ] **Step 1: Confirm Kafka and Java Consumer health**

Expected: Kafka healthy; `/actuator/health` returns `UP`; consumer group lag is `0` before the batch.

- [ ] **Step 2: Run a controlled 20-stock batch**

Use a completed trading day not already used by the prior acceptance task. Create an acceptance-specific task type so the production `daily` idempotency key is not polluted.

Expected: Collector returns 20 valid rows; MySQL delivery is 20/20; Kafka delivery is 20/20.

- [ ] **Step 3: Reconcile Java and field values**

Expected: Java success delta is 20, failures are 0, DLT delta is 0, Kafka lag returns to 0, and MySQL values match Tushare at the declared decimal scale.

- [ ] **Step 4: Update reconciliation and release documents**

Record trade date, trace ID, counts, target schema, Java result, lag, field comparison, and the fact that the old table is pending final deletion.

- [ ] **Step 5: Run full tests and commit**

Run:

```powershell
D:\Python\python.exe -m pytest tests -q
mvn -q test
git diff --check
```

Expected: Python 90 tests pass, Java tests exit 0, and no whitespace errors.

Commit:

```powershell
git add StockAnalysisSystem/docs
git commit -m "docs: record decimal migration acceptance"
```

### Task 6: Delete the Legacy Table and Finish the Branch

**Files:**
- Use: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.3_003_drop_stock_daily_float_backup.sql`

**Interfaces:**
- Consumes: complete migration and acceptance evidence.
- Produces: only the DECIMAL `stock_daily` table remains.

- [ ] **Step 1: Run the final destructive-action gate**

Verify all of the following immediately before deletion:

```text
stock_daily uses DECIMAL
stock_daily row count equals pre-migration count plus accepted new rows
stock_daily_float_backup still has the pre-migration count
repository tests pass
real dual acceptance passes
Java failure delta is zero
Kafka lag is zero
```

Expected: every condition is true. If any condition is false, do not delete the backup.

- [ ] **Step 2: Execute the explicit cleanup SQL**

Run `V0.3_003_drop_stock_daily_float_backup.sql` only after the gate passes.

- [ ] **Step 3: Verify deletion and production table health**

Expected: `stock_daily_float_backup` does not exist; `stock_daily` exists with DECIMAL columns and expected rows.

- [ ] **Step 4: Commit remaining migration artifacts**

```powershell
git status --short
git add StockAnalysisSystem/infrastructure/mysql/migrations StockAnalysisSystem/docs
git commit -m "db: complete stock daily decimal migration"
```

- [ ] **Step 5: Push only to the personal Fork**

```powershell
git push -u origin codex/stock-daily-decimal-migration
```

Expected: branch is pushed to `ajsjsjjbshshsj/kafka-stock-project`; no direct push is made to the team repository.
