$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'launcher_process.ps1')

$testRoot = Join-Path $repoRoot ('state\diagnostics\launcher-test-' + [Guid]::NewGuid().ToString('N'))
$logsRoot = Join-Path $testRoot 'logs'
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$failLaunch = $null
$timeoutLaunch = $null
$readyLaunch = $null

function Assert-Equal {
    param($Actual, $Expected, [string]$Name)
    if ($Actual -ne $Expected) { throw "$Name expected '$Expected' but got '$Actual'" }
}

try {
    $tokens = $null
    $parseErrors = $null
    $runMaxPath = Join-Path $PSScriptRoot 'run_max.ps1'
    [System.Management.Automation.Language.Parser]::ParseFile($runMaxPath, [ref]$tokens, [ref]$parseErrors) | Out-Null
    if ($parseErrors.Count -gt 0) { throw "run_max.ps1 parse failed: $($parseErrors -join '; ')" }
    $runMaxSource = Get-Content -LiteralPath $runMaxPath -Raw
    foreach ($required in @('Start-MaxLauncherProcess', 'Wait-MaxProcessReadiness', 'Format-MaxLauncherFailure', 'Backend startup failed:')) {
        if (-not $runMaxSource.Contains($required)) { throw "run_max.ps1 is not wired to launcher diagnostics: $required" }
    }

    $failScript = Join-Path $testRoot 'fail.cmd'
    @('@echo off', 'echo SIMULATED_BACKEND_TRACEBACK 1>&2', 'exit /b 37') |
        Set-Content -LiteralPath $failScript -Encoding Ascii
    $failLaunch = Start-MaxLauncherProcess -Name 'test-fail' -CommandFile $failScript -WorkingDirectory $testRoot -LogRoot $logsRoot
    $failed = Wait-MaxProcessReadiness -Launch $failLaunch -TimeoutSeconds 10 -PollMilliseconds 25 -Probe { $null }
    Assert-Equal $failed.Status 'PROCESS_EXITED' 'early process exit status'
    Assert-Equal $failed.ExitCode 37 'early process exit code'
    $failureDetails = Format-MaxLauncherFailure -Launch $failLaunch -Port '8000' -Status $failed.Status
    if ($failureDetails -notmatch "launcher_pid=$($failLaunch.Process.Id)" -or
        $failureDetails -notmatch 'port=8000' -or $failureDetails -notmatch 'exit_code=37' -or
        $failureDetails -notmatch 'SIMULATED_BACKEND_TRACEBACK') {
        throw 'Early-exit diagnostics omitted PID, port, exit code, or captured stderr.'
    }

    $slowScript = Join-Path $testRoot 'slow.cmd'
    @('@echo off', 'echo SIMULATED_TIMEOUT_TRACE 1>&2', 'ping 127.0.0.1 -n 6 > nul', 'exit /b 0') |
        Set-Content -LiteralPath $slowScript -Encoding Ascii
    $timeoutLaunch = Start-MaxLauncherProcess -Name 'test-timeout' -CommandFile $slowScript -WorkingDirectory $testRoot -LogRoot $logsRoot
    $timedOut = Wait-MaxProcessReadiness -Launch $timeoutLaunch -TimeoutSeconds 1 -PollMilliseconds 25 -Probe { $null }
    Assert-Equal $timedOut.Status 'TIMEOUT' 'timeout status'
    $timeoutDetails = Format-MaxLauncherFailure -Launch $timeoutLaunch -Port '5173' -Status $timedOut.Status
    if ($timeoutDetails -notmatch 'status=TIMEOUT' -or $timeoutDetails -notmatch 'port=5173' -or
        $timeoutDetails -notmatch 'exit_code=STILL_RUNNING' -or $timeoutDetails -notmatch 'SIMULATED_TIMEOUT_TRACE') {
        throw 'Timeout diagnostics omitted status, port, or running-process state.'
    }
    Stop-MaxLauncherProcess -Launch $timeoutLaunch
    $timeoutLaunch.Process.Refresh()
    if (-not $timeoutLaunch.Process.HasExited) { throw 'Verified timeout process was not stopped.' }

    $readyScript = Join-Path $testRoot 'ready.cmd'
    @('@echo off', 'ping 127.0.0.1 -n 6 > nul', 'exit /b 0') |
        Set-Content -LiteralPath $readyScript -Encoding Ascii
    $readyLaunch = Start-MaxLauncherProcess -Name 'test-ready' -CommandFile $readyScript -WorkingDirectory $testRoot -LogRoot $logsRoot
    $ready = Wait-MaxProcessReadiness -Launch $readyLaunch -TimeoutSeconds 3 -PollMilliseconds 25 -Probe { @{ status = 'READY' } }
    Assert-Equal $ready.Status 'READY' 'readiness status'
    Assert-Equal $ready.Value.status 'READY' 'readiness payload'
    Stop-MaxLauncherProcess -Launch $readyLaunch
    $readyLaunch.Process.Refresh()
    if (-not $readyLaunch.Process.HasExited) { throw 'Verified readiness test process was not stopped.' }

    Write-Output 'LAUNCHER_PROCESS_SUPERVISION=PASS'
    Write-Output 'EARLY_EXIT=PASS; EXIT_CODE=37; STDERR_CAPTURED=YES'
    Write-Output 'TIMEOUT=PASS; PROCESS_REMAINS_RUNNING_UNTIL_VERIFIED_STOP=YES'
    Write-Output 'READINESS=PASS'
} finally {
    foreach ($launch in @($failLaunch, $timeoutLaunch, $readyLaunch)) {
        if ($null -ne $launch) { Stop-MaxLauncherProcess -Launch $launch }
    }
    $resolvedRoot = [System.IO.Path]::GetFullPath($testRoot)
    $resolvedParent = [System.IO.Path]::GetFullPath((Join-Path $repoRoot 'state\diagnostics'))
    if (-not $resolvedRoot.StartsWith($resolvedParent + [System.IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to remove launcher test artifacts outside the repository diagnostics directory.'
    }
    if (Test-Path -LiteralPath $resolvedRoot) { Remove-Item -LiteralPath $resolvedRoot -Recurse -Force }
}
