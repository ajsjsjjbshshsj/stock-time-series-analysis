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
$indicatorReceipt = $null
if ($env:INDICATOR_DEPLOYMENT_FILE) {
    $indicatorReceipt = $env:INDICATOR_DEPLOYMENT_FILE
    if (-not [IO.Path]::IsPathRooted($indicatorReceipt)) { $indicatorReceipt=Join-Path $project $indicatorReceipt }
    $indicatorReceipt=(Resolve-Path -LiteralPath $indicatorReceipt).Path
    if (-not $env:FLINK_REST_URL) { throw 'FLINK_REST_URL required for the opted-in projection worker.' }
}
# Decode Windows arguments: quoting every argument is legal and is used by
# recovery launchers. Substring matching misses those workers and --once.
if (-not ('NightlyProcessArguments' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NightlyProcessArguments {
    [DllImport("shell32.dll", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    private static extern IntPtr CommandLineToArgvW(string value, out int count);
    [DllImport("kernel32.dll")] private static extern IntPtr LocalFree(IntPtr value);
    public static string[] Parse(string value) {
        if (String.IsNullOrWhiteSpace(value)) return new string[0];
        int count; IntPtr memory=CommandLineToArgvW(value,out count);
        if (memory==IntPtr.Zero) throw new InvalidOperationException("Process arguments unavailable");
        try {
            if (count<1 || count>4096) throw new InvalidOperationException("Process arguments out of bounds");
            string[] result=new string[count];
            for (int i=0;i<count;i++) result[i]=Marshal.PtrToStringUni(Marshal.ReadIntPtr(memory,i*IntPtr.Size));
            return result;
        } finally { LocalFree(memory); }
    }
}
'@
}
function Has-ScriptMode($entry,[string]$basename,[string]$mode) {
    $values=$entry.Arguments
    for($position=1;$position -lt $values.Count-1;$position++) {
        # Candidate tokens can be arbitrary -c code or JSON, not valid paths.
        # Only classify lexically here; strict full script ownership follows.
        $candidate=$values[$position].Replace('/','\')
        if (($candidate -eq $basename -or $candidate.EndsWith('\'+$basename,[StringComparison]::OrdinalIgnoreCase)) -and $values[$position+1] -ceq $mode) {return $true}
    }
    return $false
}
function Has-IndicatorMode($entry) {
    $values=$entry.Arguments
    for($position=1;$position -lt $values.Count-2;$position++) {
        if($values[$position] -ceq '-m' -and $values[$position+1] -ceq 'app.indicators' -and $values[$position+2] -ceq 'worker'){return $true}
    }
    return $false
}
function Unique-Option($entry,[string]$option) {
    $values=$entry.Arguments;$positions=@()
    for($position=3;$position -lt $values.Count;$position++){if($values[$position] -ceq $option){$positions+=,$position}}
    if($positions.Count -ne 1 -or $positions[0]+1 -ge $values.Count){return $null}
    return $values[$positions[0]+1]
}
function Own-PersistentWorker($entry,[string]$script,[string]$mode) {
    $values=$entry.Arguments
    # Require the actual script argument. Do not accept -c, interpreter flags,
    # another worktree or an unresolvable relative invocation as this owner.
    return $values.Count -ge 3 -and $values[1].Replace('/','\') -eq $script.Replace('/','\') -and $values[2] -ceq $mode -and -not ($values -ccontains '--once')
}
$processes=@(Get-CimInstance Win32_Process | Where-Object {$_.Name -match '^python(w)?[.]exe$'} | ForEach-Object {
    [pscustomobject]@{Process=$_;Arguments=@([NightlyProcessArguments]::Parse($_.CommandLine))}
})
$collectors=@($processes | Where-Object {Has-ScriptMode $_ 'prepare_stock_pool_data.py' 'worker'})
$analysis=@($processes | Where-Object {Has-ScriptMode $_ 'training_worker.py' 'run'})
$indicators=@($processes | Where-Object {Has-IndicatorMode $_})
if ($collectors.Count -gt 1 -or $analysis.Count -gt 1 -or $indicators.Count -gt 1) { throw 'Multiple workers detected; inspect ownership before continuing.' }
if ($indicators.Count -eq 1) {
    $values=$indicators[0].Arguments
    if (-not $indicatorReceipt -or $values.Count -lt 4 -or $values[1] -cne '-m' -or $values[2] -cne 'app.indicators' -or $values[3] -cne 'worker' -or (Unique-Option $indicators[0] '--deployment-file') -ne $indicatorReceipt -or $values -ccontains '--once') {
        throw 'Existing indicator worker has a different pinned deployment or temporary mode; inspect before replacement.'
    }
}
if ($collectors.Count -eq 1 -and (-not (Own-PersistentWorker $collectors[0] $collectorScript 'worker') -or @($collectors[0].Arguments | Where-Object {$_ -ceq '--nightly'}).Count -ne 1 -or (Unique-Option $collectors[0] '--nightly-at') -cne '20:00' -or (Unique-Option $collectors[0] '--bootstrap-start') -cne $start)) {
    throw 'Existing collection worker has a different nightly boundary; stop it safely before changing scope.'
}
if ($analysis.Count -eq 1 -and -not (Own-PersistentWorker $analysis[0] $analysisScript 'run')) {
    throw 'Existing analysis worker identity or persistent mode cannot be verified; stop it safely before replacing it.'
}
if ($CheckOnly) {
    @{run_at='20:00';start_date=$start;collector_workers=$collectors.Count;analysis_workers=$analysis.Count;indicator_workers=$indicators.Count;indicator_opt_in=($null -ne $indicatorReceipt)} | ConvertTo-Json -Compress
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
if ($indicatorReceipt -and $indicators.Count -eq 0) {
    $arguments='-m app.indicators worker --deployment-file "'+$indicatorReceipt+'"'
    $child=Start-Process $python -ArgumentList $arguments -WorkingDirectory $collector -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs "nightly-indicators-$stamp.out.log") -RedirectStandardError (Join-Path $logs "nightly-indicators-$stamp.err.log")
    if ($child.WaitForExit(3000)) { throw 'Indicator worker exited during startup; inspect pinned deployment and runtime state.' }
}
'Nightly workers requested; verify queue outcomes, not just process liveness.'
