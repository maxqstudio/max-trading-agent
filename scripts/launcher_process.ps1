function Start-MaxLauncherProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][ValidatePattern('^[a-z0-9-]+$')][string]$Name,
        [Parameter(Mandatory = $true)][string]$CommandFile,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$LogRoot
    )

    New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
    $stamp = [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
    $stdoutPath = Join-Path $LogRoot ($Name + '-' + $stamp + '.stdout.log')
    $stderrPath = Join-Path $LogRoot ($Name + '-' + $stamp + '.stderr.log')
    $exitCodePath = Join-Path $LogRoot ($Name + '-' + $stamp + '.exitcode.log')
    $wrapperPath = Join-Path $LogRoot ($Name + '-' + $stamp + '.wrapper.cmd')
    $wrapper = @(
        '@echo off',
        ('call "' + $CommandFile + '"'),
        'set "_MAX_LAUNCH_EXIT=%ERRORLEVEL%"',
        ('>"' + $exitCodePath + '" echo %_MAX_LAUNCH_EXIT%'),
        'exit /b %_MAX_LAUNCH_EXIT%'
    )
    [System.IO.File]::WriteAllLines($wrapperPath, $wrapper, [System.Text.Encoding]::ASCII)

    try {
        $process = Start-Process -FilePath $env:COMSPEC -ArgumentList @('/d', '/c', ('"' + $wrapperPath + '"')) `
            -WorkingDirectory $WorkingDirectory -WindowStyle Minimized -PassThru `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
    } catch {
        if (Test-Path -LiteralPath $wrapperPath) { Remove-Item -LiteralPath $wrapperPath -Force }
        throw
    }

    return [PSCustomObject]@{
        Name = $Name
        Process = $process
        StdoutPath = $stdoutPath
        StderrPath = $stderrPath
        ExitCodePath = $exitCodePath
        WrapperPath = $wrapperPath
    }
}

function Get-MaxLauncherExitCode {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Launch)

    if (-not (Test-Path -LiteralPath $Launch.ExitCodePath -PathType Leaf)) { return $null }
    $raw = (Get-Content -LiteralPath $Launch.ExitCodePath -Raw -ErrorAction Stop).Trim()
    $code = 0
    if ([int]::TryParse($raw, [ref]$code) -and $code -ge 0 -and $code -le 255) { return $code }
    return $null
}

function Complete-MaxLauncherProcess {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Launch)

    $process = $Launch.Process
    $process.Refresh()
    if (-not $process.HasExited) { return }
    $process.WaitForExit()
    if (Test-Path -LiteralPath $Launch.WrapperPath) {
        Remove-Item -LiteralPath $Launch.WrapperPath -Force -ErrorAction SilentlyContinue
    }
}

function Wait-MaxProcessReadiness {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Launch,
        [Parameter(Mandatory = $true)][ValidateRange(1, 600)][int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)][scriptblock]$Probe,
        [ValidateRange(10, 5000)][int]$PollMilliseconds = 250
    )

    $process = $Launch.Process
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $value = & $Probe
        if ($null -ne $value) {
            return [PSCustomObject]@{ Status = 'READY'; Value = $value; ExitCode = $null }
        }

        $process.Refresh()
        if ($process.HasExited) {
            Complete-MaxLauncherProcess -Launch $Launch
            return [PSCustomObject]@{ Status = 'PROCESS_EXITED'; Value = $null; ExitCode = (Get-MaxLauncherExitCode -Launch $Launch) }
        }
        Start-Sleep -Milliseconds $PollMilliseconds
    }

    $process.Refresh()
    if ($process.HasExited) {
        Complete-MaxLauncherProcess -Launch $Launch
        return [PSCustomObject]@{ Status = 'PROCESS_EXITED'; Value = $null; ExitCode = (Get-MaxLauncherExitCode -Launch $Launch) }
    }
    return [PSCustomObject]@{ Status = 'TIMEOUT'; Value = $null; ExitCode = $null }
}

function Stop-MaxLauncherProcess {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)]$Launch)

    $process = $Launch.Process
    if ($null -eq $process) { return }
    $process.Refresh()
    if (-not $process.HasExited) {
        & taskkill.exe /PID $process.Id /T /F *> $null
        try { $process.WaitForExit(2000) | Out-Null } catch { }
    }
    Complete-MaxLauncherProcess -Launch $Launch
}

function Format-MaxLauncherFailure {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]$Launch,
        [Parameter(Mandatory = $true)][string]$Port,
        [Parameter(Mandatory = $true)][ValidateSet('PROCESS_EXITED', 'TIMEOUT')][string]$Status
    )

    $process = $Launch.Process
    Complete-MaxLauncherProcess -Launch $Launch
    $process.Refresh()
    $exitCode = Get-MaxLauncherExitCode -Launch $Launch
    $exit = if ($null -ne $exitCode) { [string]$exitCode } elseif (-not $process.HasExited) { 'STILL_RUNNING' } elseif ($Status -eq 'TIMEOUT') { 'TERMINATED_AFTER_TIMEOUT' } else { 'EXIT_CODE_UNAVAILABLE' }
    $summary = "service=$($Launch.Name); status=$Status; launcher_pid=$($process.Id); port=$Port; exit_code=$exit; stdout_log=$($Launch.StdoutPath); stderr_log=$($Launch.StderrPath)"
    $tail = [System.Collections.Generic.List[string]]::new()
    foreach ($entry in @(@{ Label = 'stderr'; Path = $Launch.StderrPath }, @{ Label = 'stdout'; Path = $Launch.StdoutPath })) {
        if (Test-Path -LiteralPath $entry.Path -PathType Leaf) {
            try {
                $lines = @(Get-Content -LiteralPath $entry.Path -Tail 30 -ErrorAction Stop)
                if ($lines.Count -gt 0) {
                    $tail.Add("--- $($entry.Label) tail ---")
                    foreach ($line in $lines) { $tail.Add([string]$line) }
                }
            } catch {
                $tail.Add("--- $($entry.Label) tail unavailable: $($_.Exception.Message) ---")
            }
        }
    }
    if ($tail.Count -eq 0) { return $summary }
    return $summary + [Environment]::NewLine + ($tail -join [Environment]::NewLine)
}
