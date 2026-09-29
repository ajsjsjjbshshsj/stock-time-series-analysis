# GitHub Repository Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the audited latest StockAnalysisSystem with full Git history to a new public GitHub repository, verify a fresh clone, and remove obsolete local copies.

**Architecture:** The clean `codex/flink-realtime-pipeline` worktree is the migration source because every older local commit is its ancestor. GitHub receives only the canonical `main`, the active Flink branch, and tags; a fresh standalone clone becomes the only retained local project after SHA, tree, secret, ignore, and test verification.

**Tech Stack:** Git, GitHub CLI 2.101+, PowerShell, Python 3.12, pytest, Java 21, Maven 3.9+, GitHub public repository.

## Global Constraints

- GitHub owner is exactly `ajsjsjjbshshsj`.
- Repository name is exactly `stock-time-series-analysis`.
- Repository visibility is public.
- Repository description is `对股票时间序列的分析、预测、建模及可视化`.
- Canonical local target is exactly `F:/java-stock-analysis/stock-time-series-analysis`.
- Preserve full commit ancestry, the active Flink branch, and tags.
- Do not push any migration commit back to GitLab.
- Do not upload `.env`, credentials, IDE files, JDK binaries, caches, or local virtual environments.
- Do not delete old directories until the fresh GitHub clone and all verification gates pass.
- Do not delete the GitLab remote repository; it is abandoned by removing it from the new clone's workflow.

---

### Task 1: Finalize and validate the migration source

**Files:**
- Create: `docs/superpowers/plans/2026-09-29-github-migration.md`
- Read: `docs/superpowers/specs/2026-09-29-github-migration-design.md`

**Interfaces:**
- Consumes: clean branch `codex/flink-realtime-pipeline` and audited head ancestry
- Produces: one committed, clean local source HEAD used by all later SHA comparisons

- [ ] **Step 1: Commit this implementation plan locally**

```powershell
git diff --check
git add docs/superpowers/plans/2026-09-29-github-migration.md
git commit -m "docs: plan github repository migration"
```

Expected: one new commit; no push to either GitLab remote.

- [ ] **Step 2: Verify the source worktree is clean and record immutable identifiers**

```powershell
git status --short
git rev-parse HEAD
git rev-parse 'HEAD^{tree}'
```

Expected: empty status output and non-empty commit/tree SHAs.

- [ ] **Step 3: Verify every older candidate commit is already contained**

```powershell
$oldHeads = @(
  '788b868225d42065fffc63ce2425c43fdb6281f6',
  '9c15887894e3a9e5d911c7209740b5845f81d77c',
  '722f7b157c6011c65b454867098e3a92e162da17'
)
foreach ($sha in $oldHeads) {
  git merge-base --is-ancestor $sha HEAD
  if ($LASTEXITCODE -ne 0) { throw "$sha is not in the migration source" }
}
```

Expected: exit code 0 for all three commits.

### Task 2: Run the public-repository safety gate

**Files:**
- Inspect: every file tracked by `HEAD`
- Inspect: `StockAnalysisSystem/.gitignore`

**Interfaces:**
- Consumes: committed source HEAD from Task 1
- Produces: evidence that the source is safe to publish

- [ ] **Step 1: Reject tracked environment or credential files**

```powershell
$forbidden = git ls-tree -r --name-only HEAD | Where-Object {
  $_ -match '(^|/)(\.env($|\.)|credentials?\.|secrets?\.)' -and
  $_ -notmatch '\.env\.example$'
}
if ($forbidden) { $forbidden; throw 'tracked sensitive file candidates found' }
```

Expected: no forbidden tracked paths.

- [ ] **Step 2: Scan tracked text without printing secret values**

```powershell
$suspectPatterns = 'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}'
$suspectFiles = git grep -I -l -E $suspectPatterns HEAD -- 2>$null
if ($suspectFiles) { $suspectFiles; throw 'credential-like content found' }
```

Expected: no file names.

- [ ] **Step 3: Reject GitHub-incompatible large tracked files**

```powershell
$large = foreach ($path in git ls-tree -r --name-only HEAD) {
  $size = [int64](git cat-file -s "HEAD`:$path")
  if ($size -ge 100MB) { [pscustomobject]@{ Path = $path; Size = $size } }
}
if ($large) { $large; throw 'tracked files at or above 100 MB found' }
```

Expected: no large tracked files.

### Task 3: Create and populate the GitHub repository

**Files:**
- External create: `https://github.com/ajsjsjjbshshsj/stock-time-series-analysis`
- Local Git config: temporary remote named `github`

**Interfaces:**
- Consumes: safe committed source HEAD
- Produces: public GitHub `main`, active Flink branch, and tags

- [ ] **Step 1: Reconfirm identity and repository absence**

```powershell
gh auth status
gh repo view ajsjsjjbshshsj/stock-time-series-analysis
```

Expected: authenticated as `ajsjsjjbshshsj`; `repo view` reports that the repository does not exist.

- [ ] **Step 2: Create an empty public repository**

```powershell
gh repo create ajsjsjjbshshsj/stock-time-series-analysis --public --description '对股票时间序列的分析、预测、建模及可视化'
```

Expected: repository URL is returned; GitHub does not generate an unrelated README or initial commit.

- [ ] **Step 3: Add a dedicated GitHub remote and push canonical refs**

```powershell
git remote add github https://github.com/ajsjsjjbshshsj/stock-time-series-analysis.git
git push github HEAD:main
git push github HEAD:codex/flink-realtime-pipeline
git push github --tags
```

Expected: all pushes succeed. Do not use `--mirror` and do not push to `origin` or `upstream` because those still refer to GitLab in the source worktree.

- [ ] **Step 4: Verify GitHub commit identity and default branch**

```powershell
$sourceHead = git rev-parse HEAD
$remoteMain = (git ls-remote github refs/heads/main).Split("`t")[0]
if ($sourceHead -ne $remoteMain) { throw 'GitHub main SHA mismatch' }
gh repo edit ajsjsjjbshshsj/stock-time-series-analysis --default-branch main
gh repo view ajsjsjjbshshsj/stock-time-series-analysis --json nameWithOwner,visibility,defaultBranchRef,url
```

Expected: public repository, default branch `main`, and matching main SHA.

### Task 4: Create and verify the canonical fresh clone

**Files:**
- Create directory: `F:/java-stock-analysis/stock-time-series-analysis`
- Copy locally: `F:/java-stock-analysis/kafka-stock-project/StockAnalysisSystem/.env` to `F:/java-stock-analysis/stock-time-series-analysis/StockAnalysisSystem/.env`

**Interfaces:**
- Consumes: verified GitHub repository and newest ignored local `.env`
- Produces: standalone canonical local project with only GitHub `origin`

- [ ] **Step 1: Require an absent target and clone GitHub `main`**

```powershell
$target = 'F:\java-stock-analysis\stock-time-series-analysis'
if (Test-Path -LiteralPath $target) { throw "target already exists: $target" }
git clone https://github.com/ajsjsjjbshshsj/stock-time-series-analysis.git $target
```

Expected: clone succeeds and checks out `main`.

- [ ] **Step 2: Copy the ignored runtime configuration without displaying it**

```powershell
$sourceEnv = 'F:\java-stock-analysis\kafka-stock-project\StockAnalysisSystem\.env'
$targetEnv = 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem\.env'
Copy-Item -LiteralPath $sourceEnv -Destination $targetEnv
git -C 'F:\java-stock-analysis\stock-time-series-analysis' check-ignore -q StockAnalysisSystem/.env
if ($LASTEXITCODE -ne 0) { throw '.env is not ignored in the new clone' }
```

Expected: `.env` exists locally and remains untracked/ignored.

- [ ] **Step 3: Compare source, remote, and clone identities**

```powershell
$source = 'F:\java-stock-analysis\kafka-consumer-service\worktrees\unify-market-data-ingestion'
$clone = 'F:\java-stock-analysis\stock-time-series-analysis'
$sourceHead = git -C $source rev-parse HEAD
$cloneHead = git -C $clone rev-parse HEAD
$sourceTree = git -C $source rev-parse 'HEAD^{tree}'
$cloneTree = git -C $clone rev-parse 'HEAD^{tree}'
if ($sourceHead -ne $cloneHead -or $sourceTree -ne $cloneTree) {
  throw 'fresh clone does not match migration source'
}
git -C $clone remote -v
git -C $clone status --short
```

Expected: equal commit/tree SHAs, only GitHub `origin`, and clean status.

### Task 5: Run full baseline verification from the GitHub clone

**Files:**
- Test: `StockAnalysisSystem/python-services/python-collector/tests/`
- Test: `StockAnalysisSystem/python-services/stock-analysis-app/tests/`
- Test: `StockAnalysisSystem/java-services/`

**Interfaces:**
- Consumes: standalone fresh clone with ignored `.env`
- Produces: final cleanup authorization gate

- [ ] **Step 1: Verify Python Collector**

```powershell
Set-Location 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem\python-services\python-collector'
D:\Python\python.exe -m pytest tests -q
```

Expected: 118 tests pass.

- [ ] **Step 2: Verify Stock Analysis App**

```powershell
$env:POLARS_SKIP_CPU_CHECK = '1'
Set-Location 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem\python-services\stock-analysis-app'
D:\Python\python.exe -m pytest -q
```

Expected: 44 tests pass and 6 are skipped.

- [ ] **Step 3: Verify Java modules**

```powershell
Set-Location 'F:\java-stock-analysis\stock-time-series-analysis\StockAnalysisSystem\java-services'
mvn -q test
```

Expected: Maven exits with code 0.

- [ ] **Step 4: Recheck repository integrity after tests**

```powershell
git -C 'F:\java-stock-analysis\stock-time-series-analysis' status --short
git -C 'F:\java-stock-analysis\stock-time-series-analysis' remote -v
```

Expected: no tracked changes; both `origin` URLs point to GitHub.

### Task 6: Remove obsolete local project copies

**Files:**
- Delete: `F:/java-stock-analysis/kafka-stock-project`
- Delete: `F:/java-stock-analysis/kafka-consumer-service`
- Preserve: `F:/java-stock-analysis/stock-time-series-analysis`

**Interfaces:**
- Consumes: all Task 4 and Task 5 verification evidence
- Produces: exactly one local project copy

- [ ] **Step 1: Resolve and validate exact destructive targets**

```powershell
$keep = [System.IO.Path]::GetFullPath('F:\java-stock-analysis\stock-time-series-analysis').TrimEnd('\')
$targets = @(
  [System.IO.Path]::GetFullPath('F:\java-stock-analysis\kafka-stock-project').TrimEnd('\'),
  [System.IO.Path]::GetFullPath('F:\java-stock-analysis\kafka-consumer-service').TrimEnd('\')
)
$expected = @(
  'F:\java-stock-analysis\kafka-stock-project',
  'F:\java-stock-analysis\kafka-consumer-service'
)
if (Compare-Object $targets $expected) { throw 'destructive target mismatch' }
if ($targets -contains $keep) { throw 'keep path is inside destructive targets' }
$targets | ForEach-Object { Get-Item -LiteralPath $_ | Select-Object FullName }
```

Expected: exactly the two obsolete roots are printed; the canonical GitHub clone is not included.

- [ ] **Step 2: Remove linked worktrees through Git before deleting the common repository**

Run from the canonical clone, not from either deletion target:

```powershell
$common = 'F:\java-stock-analysis\kafka-stock-project'
git -C $common worktree remove --force 'F:\java-stock-analysis\kafka-consumer-service\worktrees\stock-daily-decimal-migration'
git -C $common worktree remove --force 'F:\java-stock-analysis\kafka-consumer-service\worktrees\unify-market-data-ingestion'
git -C $common worktree prune
```

Expected: both linked worktrees are unregistered before their common `.git` directory is removed.

- [ ] **Step 3: Delete the two verified obsolete roots in PowerShell**

```powershell
Remove-Item -LiteralPath 'F:\java-stock-analysis\kafka-stock-project' -Recurse -Force
Remove-Item -LiteralPath 'F:\java-stock-analysis\kafka-consumer-service' -Recurse -Force
```

Expected: both paths no longer exist. These deletes are not recoverable through Git for ignored files, which is why they run only after `.env` copy and all validation gates.

- [ ] **Step 4: Perform the final retained-copy check**

```powershell
Test-Path -LiteralPath 'F:\java-stock-analysis\stock-time-series-analysis'
Test-Path -LiteralPath 'F:\java-stock-analysis\kafka-stock-project'
Test-Path -LiteralPath 'F:\java-stock-analysis\kafka-consumer-service'
git -C 'F:\java-stock-analysis\stock-time-series-analysis' status --short
git -C 'F:\java-stock-analysis\stock-time-series-analysis' remote -v
```

Expected: `True`, `False`, `False`; clean status; GitHub-only `origin`.

