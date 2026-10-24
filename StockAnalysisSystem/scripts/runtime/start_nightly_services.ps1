param(
    [string]$PythonExecutable = 'python',
    [switch]$CheckOnly,
    [switch]$RegisterTask
)
# Start this entry through Task Scheduler or WMI, not a session-owned job.
# No credentials are placed in process arguments, console output or task XML.
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$environmentFile = Join-Path $project '.env'
if (Test-Path -LiteralPath $environmentFile) {
    Get-Content -LiteralPath $environmentFile | Where-Object { $_ -match '^[A-Za-z_][A-Za-z_0-9]*=' } | ForEach-Object {
        $key, $value = $_ -split '=', 2
        Set-Item "env:$($key.Trim())" $value.Trim()
    }
}
$start = $env:NIGHTLY_BOOTSTRAP_START
$day = [datetime]::MinValue
$china = [TimeZoneInfo]::FindSystemTimeZoneById('China Standard Time')
$today = [TimeZoneInfo]::ConvertTimeFromUtc([datetime]::UtcNow, $china).Date
if (-not [datetime]::TryParseExact($start, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$day) -or $day -lt [datetime]'2023-01-01' -or $day -gt $today) {
    throw 'NIGHTLY_BOOTSTRAP_START must be an explicit completed-data continuation date, >=2023-01-01 and not future.'
}
if ($env:AUTH_MODE -ne 'protected') { throw 'AUTH_MODE=protected required.' }
if ($env:NIGHTLY_UPDATE_AT -and $env:NIGHTLY_UPDATE_AT -ne '20:00') { throw 'This nightly entry requires NIGHTLY_UPDATE_AT=20:00.' }
$python = (Get-Command $PythonExecutable -ErrorAction Stop).Source
$collector = Join-Path $project 'python-services/python-collector'
$app = Join-Path $project 'python-services/stock-analysis-app'
$collectorScript = Join-Path $collector 'scripts/prepare_stock_pool_data.py'
$analysisScript = Join-Path $app 'scripts/training_worker.py'
$processes = @(Get-CimInstance Win32_Process)
$collectors = @($processes | Where-Object { $_.Name -match '^python(w)?[.]exe$' -and $_.CommandLine -match 'prepare_stock_pool_data[.]py"?\s+worker(?:\s|$)' })
$analysis = @($processes | Where-Object { $_.Name -match '^python(w)?[.]exe$' -and $_.CommandLine -match 'training_worker[.]py"?\s+run(?:\s|$)' })
if ($collectors.Count -gt 1 -or $analysis.Count -gt 1) { throw 'Multiple workers detected; inspect ownership before continuing.' }
function Own-PersistentWorker($process, [string]$script, [string]$mode) {
    $line = $process.CommandLine.Replace('/', '\')
    $path = [regex]::Escape($script.Replace('/', '\'))
    # Script must be Python's actual script argument, not a substring in -c,
    # another project/worktree, or an unresolvable relative invocation.
    $pattern = '^(?:"[^"]+"|\S+)\s+(?:"'+$path+'"|'+$path+')\s+'+$mode+'(?:\s|$)'
    return $line -match $pattern -and $line -notmatch '(?:^|\s)--once(?:\s|$)'
}
if ($collectors.Count -eq 1 -and (-not (Own-PersistentWorker $collectors[0] $collectorScript 'worker') -or $collectors[0].CommandLine -notmatch '--nightly(?:\s|$)' -or $collectors[0].CommandLine -notmatch '--nightly-at\s+20:00(?:\s|$)' -or $collectors[0].CommandLine -notmatch ('--bootstrap-start\s+'+[regex]::Escape($start)+'(?:\s|$)'))) {
    throw 'Existing collection worker has a different nightly boundary; stop it safely before changing scope.'
}
if ($analysis.Count -eq 1 -and -not (Own-PersistentWorker $analysis[0] $analysisScript 'run')) {
    throw 'Existing analysis worker identity or persistent mode cannot be verified; stop it safely before replacing it.'
}
if ($CheckOnly) {
    @{run_at='20:00';start_date=$start;collector_workers=$collectors.Count;analysis_workers=$analysis.Count} | ConvertTo-Json -Compress
    return
}
if ($RegisterTask) {
    $name = 'StockAnalysis-Nightly'
    $existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($existing -and -not @($existing.Actions | Where-Object { $_.Arguments -like ('*"'+$PSCommandPath+'"*') }).Count) {
        throw 'Scheduled task name belongs to another entry; refusing to overwrite it.'
    }
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $arguments = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -PythonExecutable "'+$python+'"'
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $project
    $daily = New-ScheduledTaskTrigger -Daily -At '20:00'
    # Explicit UTC+08 boundary, independent of the Windows display timezone.
    $daily.StartBoundary = $today.ToString('yyyy-MM-dd')+'T20:00:00+08:00'
    $login = New-ScheduledTaskTrigger -AtLogOn -User $owner
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $owner -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $name -Action $action -Trigger @($daily,$login) -Settings $settings -Principal $principal -Description 'Stock analysis: unique missing-only collector and saved-model prediction workers; no automatic training.' -Force | Out-Null
}
$logs = Join-Path $project 'logs/runtime'
New-Item -ItemType Directory -Force $logs | Out-Null
$stamp = [datetime]::UtcNow.ToString('yyyyMMdd-HHmmss')+'-'+$PID
if ($collectors.Count -eq 0) {
    $arguments = '"'+$collectorScript+'" worker --nightly --nightly-at 20:00 --bootstrap-start '+$start
    $child = Start-Process $python -ArgumentList $arguments -WorkingDirectory $collector -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "nightly-collector-$stamp.out.log") -RedirectStandardError (Join-Path $logs "nightly-collector-$stamp.err.log")
    if ($child.WaitForExit(3000)) { throw 'Collection worker exited during startup; inspect sanitized runtime logs before retry.' }
}
if ($analysis.Count -eq 0) {
    $child = Start-Process $python -ArgumentList ('"'+$analysisScript+'" run') -WorkingDirectory $app -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "nightly-analysis-$stamp.out.log") -RedirectStandardError (Join-Path $logs "nightly-analysis-$stamp.err.log")
    if ($child.WaitForExit(3000)) { throw 'Analysis worker exited during startup; inspect sanitized runtime logs before retry.' }
}
'Nightly workers requested; verify queue outcomes, not just process liveness.'
