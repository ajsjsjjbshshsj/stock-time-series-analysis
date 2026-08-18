# Unified Market Data Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `python-collector` the only Tushare/AkShare integration point while preserving the analysis application's data columns, commands, caches, and historical behavior through MySQL-backed compatibility adapters.

**Architecture:** External SDK calls live behind provider clients inside `python-collector`; Collector adapters normalize them and independent jobs persist OHLCV, daily-basic, and constituent data. `stock-analysis-app` replaces network access with a repository that joins the persisted tables, while its existing `DataCollector` remains as a temporary DB-backed compatibility facade.

**Tech Stack:** Python 3.12, pandas, Tushare, AkShare, SQLAlchemy, PyMySQL, MySQL 8, pytest, Docker.

## Global Constraints

- Only `python-collector` may import or call Tushare/AkShare after the migration.
- `stock-analysis-app` must not access external market-data APIs.
- Existing OHLCV Kafka contract `StockDailyEvent` is unchanged.
- Existing `mysql`, `kafka`, and `dual` output-mode semantics remain unchanged for OHLCV.
- Complete analysis compatibility requires `mysql` or `dual` because analysis reads MySQL.
- `stock_daily_basic` stores source-provided raw values; `stock_features` remains derived output.
- Price/ratio fields use `DECIMAL(20,6)` and market-value fields use `DECIMAL(24,4)`.
- Historical backfill is idempotent, resumable, rate-limited, and bounded by dates present in `stock_daily`.
- Existing Parquet, model, and Streamlit caches remain intact.
- Secrets are loaded from ignored local environment files and are never committed or printed.
- Push only to the personal Fork; create a Merge Request from that Fork to the team repository.

---

### Task 1: Centralize Security-Code Conversion and SDK Calls

**Files:**
- Create: `StockAnalysisSystem/python-services/python-collector/app/market_data/__init__.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/market_data/codes.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/market_data/tushare_client.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/market_data/akshare_client.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_market_data_codes.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_market_data_clients.py`

**Interfaces:**
- Consumes: injected Tushare Pro client or AkShare module.
- Produces: `to_ts_code(code: str) -> str`, `TushareClient`, and `AkshareClient` for Collector adapters.

- [ ] **Step 1: Write failing code-conversion tests**

```python
from app.market_data.codes import to_ts_code


def test_to_ts_code_supports_all_a_share_exchanges():
    assert to_ts_code("600000") == "600000.SH"
    assert to_ts_code("000001") == "000001.SZ"
    assert to_ts_code("300001") == "300001.SZ"
    assert to_ts_code("430047") == "430047.BJ"
    assert to_ts_code("830799") == "830799.BJ"
    assert to_ts_code("600000.sh") == "600000.SH"
```

- [ ] **Step 2: Run the test and verify the module is missing**

Run: `D:\Python\python.exe -m pytest tests\test_market_data_codes.py -v`

Expected: FAIL with `ModuleNotFoundError: app.market_data`.

- [ ] **Step 3: Implement the shared conversion utility**

```python
def to_ts_code(code: str) -> str:
    normalized = str(code).strip().upper()
    if "." in normalized:
        return normalized
    if normalized.startswith(("4", "8")):
        return f"{normalized}.BJ"
    if normalized.startswith(("6", "9")):
        return f"{normalized}.SH"
    return f"{normalized}.SZ"
```

- [ ] **Step 4: Write failing provider delegation tests**

```python
def test_tushare_client_delegates_daily_basic():
    pro = Mock()
    client = TushareClient(pro)
    client.daily_basic(trade_date="20260814")
    pro.daily_basic.assert_called_once_with(trade_date="20260814")


def test_akshare_client_delegates_history():
    sdk = Mock()
    client = AkshareClient(sdk)
    client.stock_history("000001", "20260801", "20260814")
    sdk.stock_zh_a_hist.assert_called_once_with(
        symbol="000001",
        period="daily",
        start_date="20260801",
        end_date="20260814",
        adjust="qfq",
    )
```

- [ ] **Step 5: Implement provider clients without configuration imports**

`TushareClient` exposes `daily`, `daily_basic`, `stock_basic`, `trade_calendar`, and `index_weight`. `AkshareClient` exposes `stock_history`, `stock_info`, `industry_constituents`, and `index_constituents`. Each method delegates to the injected SDK and lets exceptions propagate.

- [ ] **Step 6: Run provider and code tests**

Run: `D:\Python\python.exe -m pytest tests\test_market_data_codes.py tests\test_market_data_clients.py -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit**

```powershell
git add StockAnalysisSystem/python-services/python-collector/app/market_data StockAnalysisSystem/python-services/python-collector/tests/test_market_data_codes.py StockAnalysisSystem/python-services/python-collector/tests/test_market_data_clients.py
git commit -m "refactor: centralize market data sdk clients"
```

### Task 2: Extend Collector Contracts and Remove Adapter Duplication

**Files:**
- Modify: `StockAnalysisSystem/python-services/python-collector/app/collectors/base_collector.py`
- Modify: `StockAnalysisSystem/python-services/python-collector/app/collectors/tushare_collector.py`
- Modify: `StockAnalysisSystem/python-services/python-collector/app/collectors/akshare_collector.py`
- Modify: `StockAnalysisSystem/python-services/python-collector/tests/test_tushare_collector.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_akshare_collector.py`

**Interfaces:**
- Consumes: Task 1 provider clients and `to_ts_code`.
- Produces: normalized daily-basic and constituent DataFrames.

- [ ] **Step 1: Add failing Tushare daily-basic contract test**

```python
def test_collect_daily_basic_uses_protocol_columns():
    collector = TushareCollector(token="test", client=client)
    result = collector.collect_daily_basic("2026-08-14")
    assert list(result.columns) == [
        "ts_code", "trade_date", "turnover_rate",
        "pe", "pe_ttm", "pb", "ps", "total_mv",
    ]
    assert result.iloc[0]["trade_date"].isoformat() == "2026-08-14"
```

- [ ] **Step 2: Add default optional methods to BaseCollector**

Add concrete methods returning an empty DataFrame so existing test doubles do not break:

```python
def collect_daily_basic(self, trade_date: str) -> pd.DataFrame:
    return pd.DataFrame()

def collect_index_constituents(self, index_code: str, as_of_date: str) -> pd.DataFrame:
    return pd.DataFrame()

def collect_industry_constituents(self, industry_code: str, as_of_date: str) -> pd.DataFrame:
    return pd.DataFrame()
```

- [ ] **Step 3: Refactor TushareCollector to use injected TushareClient**

The constructor keeps `token` compatibility, creates the SDK only when no client is injected, and removes its local `to_tushare_code`. Implement `collect_daily_basic` and `collect_index_constituents`; unsupported industry collection returns empty with a warning.

- [ ] **Step 4: Add failing AkShare normalization test**

```python
def test_akshare_daily_maps_turnover_rate():
    result = collector.collect_daily_single("000001", "20260814", "20260814")
    assert result.iloc[0]["ts_code"] == "000001.SZ"
    assert result.iloc[0]["turnover_rate"] == 1.25
```

- [ ] **Step 5: Refactor AkShareCollector to use AkshareClient**

Map `换手率` to `turnover_rate`, use `to_ts_code`, and implement index/industry constituent collection. `collect_daily_basic` returns the daily rows reduced to the shared daily-basic columns, with valuation columns set to `None`.

- [ ] **Step 6: Run Collector contract tests**

Run: `D:\Python\python.exe -m pytest tests\test_tushare_collector.py tests\test_akshare_collector.py tests\test_market_data_codes.py tests\test_market_data_clients.py -v`

Expected: PASS.

- [ ] **Step 7: Run the existing Collector suite**

Run: `D:\Python\python.exe -m pytest -q`

Expected: at least the original 90 tests plus new tests pass.

- [ ] **Step 8: Commit**

```powershell
git add StockAnalysisSystem/python-services/python-collector/app/collectors StockAnalysisSystem/python-services/python-collector/tests
git commit -m "feat: normalize tushare and akshare collectors"
```

### Task 3: Add Raw Daily-Basic and Constituent Storage

**Files:**
- Create: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.4_001_market_reference_tables.sql`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/database/models.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/repositories/market_data_repository.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_market_data_repository.py`

**Interfaces:**
- Consumes: normalized record dictionaries from Task 2.
- Produces: `save_daily_basic(records)`, `save_constituents(records)`, and query helpers for backfill.

- [ ] **Step 1: Write the migration SQL**

Create `stock_daily_basic` and `stock_constituent` with the exact schema from the approved design. Use `DECIMAL(20,6)` for ratios and weights, `DECIMAL(24,4)` for `total_mv`, and the declared unique indexes.

- [ ] **Step 2: Add matching SQLAlchemy models and table constants**

Use `Numeric(20, 6)` and `Numeric(24, 4)`, not `Float`.

- [ ] **Step 3: Write failing repository idempotency tests**

```python
def test_save_daily_basic_uses_business_key_upsert(session):
    repo.save_daily_basic([record])
    repo.save_daily_basic([{**record, "pb": Decimal("1.500000")}])
    assert count_rows(session, "stock_daily_basic") == 1
    assert load_pb(session, record) == Decimal("1.500000")
```

- [ ] **Step 4: Implement MySQL upserts**

Use `INSERT ... ON DUPLICATE KEY UPDATE` for both tables. Return the number of input records accepted, and execute in batches of 5,000.

- [ ] **Step 5: Add trade-date helpers**

Implement:

```python
list_stock_daily_trade_dates(start_date=None, end_date=None) -> list[date]
count_daily_basic_by_date(trade_date: date) -> int
```

- [ ] **Step 6: Run repository tests and SQL syntax checks**

Run: `D:\Python\python.exe -m pytest tests\test_market_data_repository.py -v`

Expected: PASS using a mocked SQLAlchemy session or controlled test database fixture.

- [ ] **Step 7: Commit**

```powershell
git add StockAnalysisSystem/infrastructure/mysql/migrations/V0.4_001_market_reference_tables.sql StockAnalysisSystem/python-services/stock-analysis-app/database/models.py StockAnalysisSystem/python-services/python-collector/app/repositories/market_data_repository.py StockAnalysisSystem/python-services/python-collector/tests/test_market_data_repository.py
git commit -m "db: add raw market reference tables"
```

### Task 4: Add Independent Daily-Basic and Constituent Jobs

**Files:**
- Modify: `StockAnalysisSystem/python-services/python-collector/app/models/collection_task.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/jobs/daily_basic_collection_job.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/jobs/daily_basic_backfill_job.py`
- Create: `StockAnalysisSystem/python-services/python-collector/app/jobs/constituent_collection_job.py`
- Modify: `StockAnalysisSystem/python-services/python-collector/app/main.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_daily_basic_jobs.py`
- Create: `StockAnalysisSystem/python-services/python-collector/tests/test_constituent_collection_job.py`

**Interfaces:**
- Consumes: Collector methods from Task 2 and repositories from Task 3.
- Produces: resumable CLI commands and independent collection-task status.

- [ ] **Step 1: Add task types**

```python
class TaskType(str, Enum):
    DAILY = "daily"
    DAILY_BASIC = "daily_basic"
    CONSTITUENT = "constituent"
    HISTORY = "history"
    BASIC = "basic"
    RETRY = "retry"
```

- [ ] **Step 2: Write failing independence test**

```python
def test_daily_basic_failure_does_not_change_daily_task():
    result = job.execute("20260814")
    assert result["success"] is False
    assert task_repo.status_for("daily_basic", "20260814") == "FAILED"
    assert task_repo.status_for("daily", "20260814") == "SUCCESS"
```

- [ ] **Step 3: Implement DailyBasicCollectionJob**

Normalize the date, create an idempotent `daily_basic` task, collect one date, validate business keys, upsert records, and update only that task.

- [ ] **Step 4: Write failing resumable backfill test**

```python
def test_backfill_skips_success_and_retries_failed_dates():
    result = job.execute("20260801", "20260805")
    assert collector.requested_dates == ["20260804", "20260805"]
    assert result == {"success_count": 2, "failed_count": 0, "skipped_count": 1}
```

- [ ] **Step 5: Implement bounded backfill**

Read actual dates from `stock_daily`, process one date at a time, honor `COLLECTION_REQUEST_INTERVAL`, and never hold multiple dates in memory.

- [ ] **Step 6: Implement constituent job**

The job accepts `group_type`, `group_code`, and `as_of_date`, normalizes results to the table contract, and stores one historical snapshot.

- [ ] **Step 7: Add CLI commands**

```text
daily-basic --date YYYYMMDD
daily-basic-history --start YYYYMMDD --end YYYYMMDD
constituent --type index|industry --code CODE --date YYYYMMDD
daily-market --date YYYYMMDD
```

`daily-market` invokes the existing OHLCV command and daily-basic command in separate database transactions. Its result reports both statuses and never rolls back a completed OHLCV transaction because daily-basic failed.

- [ ] **Step 8: Run job and CLI tests**

Run: `D:\Python\python.exe -m pytest tests\test_daily_basic_jobs.py tests\test_constituent_collection_job.py tests\test_daily_collection_job.py -v`

Expected: PASS.

- [ ] **Step 9: Commit**

```powershell
git add StockAnalysisSystem/python-services/python-collector/app StockAnalysisSystem/python-services/python-collector/tests
git commit -m "feat: collect daily basic and constituent data"
```

### Task 5: Add the Analysis Database Boundary

**Files:**
- Create: `StockAnalysisSystem/python-services/stock-analysis-app/data_loader/market_data_repository.py`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/database/repository.py`
- Create: `StockAnalysisSystem/python-services/stock-analysis-app/tests/test_market_data_repository.py`

**Interfaces:**
- Consumes: `stock_daily`, `stock_daily_basic`, `stock_basic`, and `stock_constituent`.
- Produces: DB-backed methods matching the approved analysis contract.

- [ ] **Step 1: Write failing JOIN contract test**

```python
def test_fetch_single_returns_old_market_columns(session):
    result = repository.fetch_single("000001.SZ", "20260801", "20260814")
    assert list(result.columns) == [
        "ts_code", "trade_date", "open", "high", "low", "close",
        "pre_close", "change", "pct_chg", "vol", "amount",
        "turnover_rate", "pe", "pe_ttm", "pb", "ps", "total_mv",
    ]
    assert pd.api.types.is_datetime64_any_dtype(result["trade_date"])
```

- [ ] **Step 2: Extend low-level database repository queries**

Add a LEFT JOIN from `stock_daily` to `stock_daily_basic` on `(ts_code, trade_date)`. A missing daily-basic row must preserve the OHLCV row and return `NULL` optional fields.

- [ ] **Step 3: Implement MarketDataRepository**

```python
class MarketDataRepository:
    def fetch_single(self, code, start_date, end_date): ...
    def fetch_stock_list(self): ...
    def load_daily_panel(self, stock_codes, start_date, end_date): ...
    def get_index_constituents(self, index_code, as_of_date=None): ...
    def get_industry_constituents(self, industry_code, as_of_date=None): ...
```

When `as_of_date` is omitted, constituent queries select the latest snapshot date for the requested group.

- [ ] **Step 4: Test missing optional data and snapshot selection**

Run: `D:\Python\python.exe -m pytest tests\test_market_data_repository.py -v --confcutdir=.`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add StockAnalysisSystem/python-services/stock-analysis-app/data_loader/market_data_repository.py StockAnalysisSystem/python-services/stock-analysis-app/database/repository.py StockAnalysisSystem/python-services/stock-analysis-app/tests/test_market_data_repository.py
git commit -m "feat: read market data from mysql"
```

### Task 6: Convert DataCollector into a DB-Backed Compatibility Facade

**Files:**
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/data_loader/collector.py`
- Create: `StockAnalysisSystem/python-services/stock-analysis-app/tests/test_data_collector_compatibility.py`

**Interfaces:**
- Consumes: `MarketDataRepository` from Task 5.
- Produces: old `DataCollector` public methods without external network calls.

- [ ] **Step 1: Write compatibility tests**

```python
def test_data_collector_keeps_fetch_single_signature(repository):
    collector = DataCollector(use_tushare=True, repository=repository)
    result = collector.fetch_single("000001.SZ", "20260801", "20260814")
    repository.fetch_single.assert_called_once_with(
        "000001.SZ", "20260801", "20260814"
    )
    assert result is repository.fetch_single.return_value


def test_data_collector_does_not_import_external_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "tushare", None)
    monkeypatch.setitem(sys.modules, "akshare", None)
    DataCollector(repository=repository).fetch_stock_list()
```

- [ ] **Step 2: Replace API construction with repository injection**

Keep `use_tushare` in the constructor for call compatibility, but log once that source selection belongs to `python-collector`. Remove retry and request-delay code because database reads do not use external API retry semantics.

- [ ] **Step 3: Preserve batch methods**

`collect_daily_data` and `collect_all_stocks` call repository panel queries and return the old `{code: DataFrame}` shape.

- [ ] **Step 4: Run compatibility tests**

Run: `D:\Python\python.exe -m pytest tests\test_data_collector_compatibility.py -v --confcutdir=.`

Expected: PASS without importing Tushare or AkShare.

- [ ] **Step 5: Commit**

```powershell
git add StockAnalysisSystem/python-services/stock-analysis-app/data_loader/collector.py StockAnalysisSystem/python-services/stock-analysis-app/tests/test_data_collector_compatibility.py
git commit -m "refactor: back analysis collector with mysql"
```

### Task 7: Remove Remaining Analysis-Side Network Access

**Files:**
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/data_processor/stock_filter.py`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/main.py`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/data_processor/panel_builder.py`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/visualization/dashboard.py`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/requirements.txt`
- Delete: `StockAnalysisSystem/python-services/stock-analysis-app/data_loader/tushare_api.py`
- Create: `StockAnalysisSystem/python-services/stock-analysis-app/tests/test_no_external_market_api.py`

**Interfaces:**
- Consumes: Tasks 5 and 6.
- Produces: an analysis service with no Tushare/AkShare imports.

- [ ] **Step 1: Write an import-boundary test**

```python
import ast
from pathlib import Path


def scan_python_imports(root: Path, forbidden: set[str]) -> list[str]:
    violations = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {item.name.split(".")[0] for item in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = {node.module.split(".")[0]}
            else:
                continue
            if names & forbidden:
                violations.append(str(path.relative_to(root)))
    return sorted(set(violations))


def test_analysis_app_has_no_tushare_or_akshare_imports():
    violations = scan_python_imports(PROJECT_ROOT, {"tushare", "akshare"})
    assert violations == []
```

- [ ] **Step 2: Replace stock filtering API calls**

`resolve_stock_codes` receives or constructs `MarketDataRepository`, reads stored constituent snapshots, preserves sorting and empty-result behavior, and no longer branches on `use_tushare`.

- [ ] **Step 3: Replace direct collection in CLI, panel, and Dashboard**

Keep user-facing commands functional while routing all reads through `DataCollector`/`MarketDataRepository`. Source-selection flags may remain accepted for compatibility but display a deprecation message.

- [ ] **Step 4: Remove external dependencies and legacy API file**

Delete `tushare_api.py`. Remove `tushare` and `akshare` from `stock-analysis-app/requirements.txt`; retain them in `python-collector/requirements.txt`.

- [ ] **Step 5: Run boundary and analysis regression tests**

Load the ignored `.env` into the process, then run:

```powershell
D:\Python\python.exe -m pytest tests\test_no_external_market_api.py tests\test_data_collector_compatibility.py tests\test_market_data_repository.py tests\test_baseline.py tests\test_cleaner.py tests\test_config.py tests\test_database.py tests\test_feature_engineer.py -q --confcutdir=.
```

Expected: all runnable tests PASS; the original 6 environment-dependent baseline tests may remain skipped.

- [ ] **Step 6: Commit**

```powershell
git add StockAnalysisSystem/python-services/stock-analysis-app
git commit -m "refactor: remove analysis-side market api access"
```

### Task 8: Preserve Cache and Feature Inputs

**Files:**
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/data_processor/panel_builder.py`
- Create: `StockAnalysisSystem/python-services/stock-analysis-app/tests/test_panel_cache_compatibility.py`

**Interfaces:**
- Consumes: joined daily/daily-basic panel.
- Produces: the same Parquet cache shapes expected by current training pipelines.

- [ ] **Step 1: Write cache compatibility tests**

```python
def test_raw_panel_cache_keeps_turnover_rate(tmp_path):
    save_raw_panel(joined_panel, tmp_path / "raw_panel.parquet")
    cached = pd.read_parquet(tmp_path / "raw_panel.parquet")
    assert "turnover_rate" in cached.columns
    assert cached.loc[0, "turnover_rate"] == joined_panel.loc[0, "turnover_rate"]
```

- [ ] **Step 2: Remove cache-only turnover-rate recovery as the primary path**

The database JOIN becomes the primary source. Existing Parquet recovery remains only as a compatibility fallback for dates not yet backfilled, with an explicit warning and no silent overwrite of non-null DB values.

- [ ] **Step 3: Verify all existing cache paths remain unchanged**

Do not rename `raw_panel.parquet`, `panel_features.parquet`, `features_*.parquet`, model files, or Streamlit cache functions.

- [ ] **Step 4: Run cache and feature tests**

Run: `D:\Python\python.exe -m pytest tests\test_panel_cache_compatibility.py tests\test_feature_engineer.py -v --confcutdir=.`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add StockAnalysisSystem/python-services/stock-analysis-app/data_processor/panel_builder.py StockAnalysisSystem/python-services/stock-analysis-app/tests/test_panel_cache_compatibility.py
git commit -m "refactor: preserve analysis cache inputs"
```

### Task 9: Apply Schema and Run Controlled Historical Backfill

**Files:**
- Use: `StockAnalysisSystem/infrastructure/mysql/migrations/V0.4_001_market_reference_tables.sql`
- Modify: `StockAnalysisSystem/docs/V0.4_MARKET_DATA_MIGRATION_REPORT.md`

**Interfaces:**
- Consumes: local ignored credentials and completed collection commands.
- Produces: populated raw tables and reproducible evidence.

- [ ] **Step 1: Apply the migration to local MySQL**

Verify the tables do not already exist, execute the SQL, and inspect column types and indexes through `information_schema`.

- [ ] **Step 2: Run a one-day Tushare acceptance batch**

Execute `daily-market` for a completed trading day not used by a prior task. Verify OHLCV and daily-basic task statuses independently and compare stored values with Tushare at declared decimal scales.

- [ ] **Step 3: Run a one-stock AkShare acceptance batch**

Verify OHLCV and `turnover_rate`; confirm unsupported valuation fields are `NULL`.

- [ ] **Step 4: Exercise backfill recovery**

Run a short multi-day range, repeat it to prove idempotency, mark one controlled task failed, and rerun to prove resume behavior.

- [ ] **Step 5: Start the full historical daily-basic backfill**

Use dates present in `stock_daily`, bounded batches, and task checkpoints. Record progress and failures without exporting credentials or raw database dumps.

- [ ] **Step 6: Write migration evidence**

Record schema, date range, expected/completed dates, row counts, duplicate-key count, sampled precision comparisons, retry evidence, and any remaining failed dates.

- [ ] **Step 7: Commit the report**

```powershell
git add StockAnalysisSystem/docs/V0.4_MARKET_DATA_MIGRATION_REPORT.md
git commit -m "docs: record market data migration acceptance"
```

### Task 10: Full Verification, Documentation, and Fork MR

**Files:**
- Modify: `StockAnalysisSystem/python-services/python-collector/README.md`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/README.md`
- Modify: `StockAnalysisSystem/python-services/stock-analysis-app/CODE_READING_GUIDE.md`
- Create: `StockAnalysisSystem/docs/V0.4_RELEASE_NOTES.md`

**Interfaces:**
- Consumes: all previous tasks.
- Produces: tested branch on the personal Fork and a team MR.

- [ ] **Step 1: Document the ownership boundary and commands**

Document Collector-only SDK access, `daily-market`, daily-basic history backfill, constituent snapshots, DB-backed analysis, cache behavior, and rollback procedures.

- [ ] **Step 2: Run the full Collector test suite**

Run: `D:\Python\python.exe -m pytest -q`

Working directory: `StockAnalysisSystem/python-services/python-collector`.

Expected: all original and new tests PASS.

- [ ] **Step 3: Run the full analysis test suite explicitly**

Load the ignored `.env` into the process and run the five original test files plus all new test files explicitly with `--confcutdir=.` to avoid the unrelated `F:\WpSystem` discovery issue.

Expected: all runnable tests PASS; expected environment skips are documented.

- [ ] **Step 4: Verify forbidden imports and repository cleanliness**

```powershell
git diff --check
git status --short
```

Also scan `stock-analysis-app` for `import tushare`, `import akshare`, and `data_loader.tushare_api`; expected result is empty.

- [ ] **Step 5: Verify the live boundary**

Run one analysis command and the Dashboard with outbound market-data access disabled. Both must read MySQL/cache successfully without constructing an external SDK client.

- [ ] **Step 6: Commit remaining documentation**

```powershell
git add StockAnalysisSystem/python-services/python-collector/README.md StockAnalysisSystem/python-services/stock-analysis-app/README.md StockAnalysisSystem/python-services/stock-analysis-app/CODE_READING_GUIDE.md StockAnalysisSystem/docs
git commit -m "docs: explain unified market data flow"
```

- [ ] **Step 7: Push only to the personal Fork**

```powershell
git push -u origin codex/unify-market-data-ingestion
```

- [ ] **Step 8: Create the team Merge Request**

Create an MR from `ajsjsjjbshshsj/kafka-stock-project:codex/unify-market-data-ingestion` to `ybw-group/kafka-stock-project:main`. Do not delete the branch until the MR is merged or no longer needed.
