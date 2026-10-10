$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendUrl = "http://127.0.0.1:8000"
$FrontendUrl = "http://127.0.0.1:5173"
$LauncherLogRoot = Join-Path $ProjectRoot "state\diagnostics\launcher"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AuthorityHelper = Join-Path $ProjectRoot "scripts\launcher_authority.py"
$ProcessHelper = Join-Path $ProjectRoot "scripts\launcher_process.ps1"
$Authority = $null
$BackendProcess = $null
$FrontendProcess = $null
$BackendLaunch = $null
$FrontendLaunch = $null

. $ProcessHelper

function Stop-StartedProcessTree {
    param([System.Diagnostics.Process]$Process)
    if ($null -eq $Process) {
        return
    }
    try {
        if (-not $Process.HasExited) {
            & taskkill.exe /PID $Process.Id /T /F *> $null
        }
    } catch {
    }
}

function Fail-Max {
    param(
        [string]$Message,
        [int]$Code
    )
    if ($null -ne $FrontendLaunch) {
        Stop-MaxLauncherProcess -Launch $FrontendLaunch
    } else {
        Stop-StartedProcessTree $FrontendProcess
    }
    if ($null -ne $BackendLaunch) {
        Stop-MaxLauncherProcess -Launch $BackendLaunch
    } else {
        Stop-StartedProcessTree $BackendProcess
    }
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    exit $Code
}

function Test-PortListening {
    param([int]$Port)
    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        foreach ($endpoint in $listeners) {
            if ([int]$endpoint.Port -eq $Port) {
                return $true
            }
        }
        return $false
    } catch {
        throw ("PORT_LISTENER_CHECK_FAILED:" + [string]$Port + ":" + $_.Exception.Message)
    }
}

function Get-Overview {
    param([string]$Url)
    try {
        return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
    } catch {
        return $null
    }
}

function Get-AuthorityProblem {
    param(
        [string]$Mode,
        $Payload
    )
    try {
        $json = $Payload | ConvertTo-Json -Depth 20 -Compress
        $output = $json | & $Python $AuthorityHelper $Mode 2>&1
        $code = $LASTEXITCODE
    } catch {
        return ("launcher authority helper failed: " + $_.Exception.Message)
    }
    if ($code -eq 0) {
        return $null
    }
    $message = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($message)) {
        return "launcher authority validation failed"
    }
    return $message
}

function Get-OverviewProblem {
    param($Overview)
    if ($null -eq $Overview) {
        return "overview endpoint unavailable"
    }
    return Get-AuthorityProblem "overview" $Overview
}

function Test-FrontendRoot {
    try {
        $response = Invoke-WebRequest -Uri ($FrontendUrl + "/") -UseBasicParsing -TimeoutSec 2
        return [int]$response.StatusCode -eq 200
    } catch {
        return $false
    }
}

function Get-ScientistStatus {
    param([string]$BaseUrl)
    try {
        return Invoke-RestMethod -Uri ($BaseUrl + "/api/scientist/status") -Method Get -TimeoutSec 3
    } catch {
        return $null
    }
}

Set-Location $ProjectRoot
Write-Host "MAX Rebuild"
Write-Host "Root: $ProjectRoot"

if (-not (Test-Path $Python -PathType Leaf)) {
    Fail-Max "Python venv missing: $Python" 23
}
if (-not (Test-Path $AuthorityHelper -PathType Leaf)) {
    Fail-Max "Launcher authority helper missing: $AuthorityHelper" 20
}
try {
    $authorityOutput = & $Python $AuthorityHelper expected 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw (($authorityOutput | Out-String).Trim())
    }
    $Authority = ($authorityOutput | Out-String) | ConvertFrom-Json
} catch {
    Fail-Max ("Launcher canonical authority unavailable: " + $_.Exception.Message) 20
}

$backendOverview = Get-Overview ($BackendUrl + "/api/overview")
if ($null -ne $backendOverview) {
    $problem = Get-OverviewProblem $backendOverview
    if ($null -ne $problem) {
        Fail-Max "Port 8000 is serving an invalid MAX backend: $problem" 21
    }
    Write-Host "[READY] Existing MAX backend verified."
} else {
    if (Test-PortListening 8000) {
        Fail-Max "Port 8000 is occupied by an unrelated or broken process." 22
    }

    $backendCmd = Join-Path $ProjectRoot "scripts\dev_backend.cmd"
    if (-not (Test-Path $backendCmd -PathType Leaf)) {
        Fail-Max "Backend runner missing: $backendCmd" 24
    }

    $BackendLaunch = Start-MaxLauncherProcess -Name "backend" -CommandFile $backendCmd `
        -WorkingDirectory $ProjectRoot -LogRoot $LauncherLogRoot
    $BackendProcess = $BackendLaunch.Process
    $backendWait = Wait-MaxProcessReadiness -Launch $BackendLaunch -TimeoutSeconds 30 -Probe {
        Get-Overview ($BackendUrl + "/api/overview")
    }
    if ($backendWait.Status -ne "READY") {
        if ($backendWait.Status -eq "TIMEOUT") { Stop-MaxLauncherProcess -Launch $BackendLaunch }
        $details = Format-MaxLauncherFailure -Launch $BackendLaunch -Port "8000" -Status $backendWait.Status
        Fail-Max ("Backend startup failed: " + $details) 25
    }
    $backendOverview = $backendWait.Value

    $problem = Get-OverviewProblem $backendOverview
    if ($null -ne $problem) {
        Fail-Max "Backend foundation state invalid: $problem" 26
    }
    Write-Host "[READY] Backend launched and verified."
}

$proxyOverview = Get-Overview ($FrontendUrl + "/api/overview")
if ($null -ne $proxyOverview) {
    $problem = Get-OverviewProblem $proxyOverview
    if ($null -ne $problem) {
        Fail-Max "Port 5173 is serving an invalid MAX frontend: $problem" 31
    }
    if (-not (Test-FrontendRoot)) {
        Fail-Max "Existing MAX frontend root did not return HTTP 200." 32
    }
    Write-Host "[READY] Existing MAX frontend verified."
} else {
    if (Test-PortListening 5173) {
        Fail-Max "Port 5173 is occupied by an unrelated or broken process." 33
    }

    if ($null -eq (Get-Command node.exe -ErrorAction SilentlyContinue)) {
        Fail-Max "Node.js is not available on PATH." 34
    }
    if ($null -eq (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Fail-Max "npm is not available on PATH." 35
    }

    $vite = Join-Path $ProjectRoot "frontend\node_modules\.bin\vite.cmd"
    if (-not (Test-Path $vite -PathType Leaf)) {
        Fail-Max "Frontend dependencies are missing. Run npm install in frontend first." 36
    }

    $frontendCmd = Join-Path $ProjectRoot "scripts\dev_frontend.cmd"
    if (-not (Test-Path $frontendCmd -PathType Leaf)) {
        Fail-Max "Frontend runner missing: $frontendCmd" 37
    }

    $FrontendLaunch = Start-MaxLauncherProcess -Name "frontend" -CommandFile $frontendCmd `
        -WorkingDirectory $ProjectRoot -LogRoot $LauncherLogRoot
    $FrontendProcess = $FrontendLaunch.Process
    $frontendWait = Wait-MaxProcessReadiness -Launch $FrontendLaunch -TimeoutSeconds 30 -Probe {
        Get-Overview ($FrontendUrl + "/api/overview")
    }
    if ($frontendWait.Status -ne "READY") {
        if ($frontendWait.Status -eq "TIMEOUT") { Stop-MaxLauncherProcess -Launch $FrontendLaunch }
        $details = Format-MaxLauncherFailure -Launch $FrontendLaunch -Port "5173" -Status $frontendWait.Status
        Fail-Max ("Frontend/Vite startup failed: " + $details) 38
    }
    $proxyOverview = $frontendWait.Value

    $problem = Get-OverviewProblem $proxyOverview
    if ($null -ne $problem) {
        Fail-Max "Vite proxy returned invalid MAX overview: $problem" 39
    }
    if (-not (Test-FrontendRoot)) {
        Fail-Max "Frontend root did not return HTTP 200." 40
    }
    Write-Host "[READY] Frontend launched and Vite proxy verified."
}

$proxyMatchProblem = Get-AuthorityProblem "match" ([PSCustomObject]@{
    backend = $backendOverview
    proxy = $proxyOverview
})
if ($null -ne $proxyMatchProblem) {
    Fail-Max ("Frontend proxy authority mismatch: " + $proxyMatchProblem) 44
}

$ScientistStatus = Get-ScientistStatus $FrontendUrl
if ($null -eq $ScientistStatus) {
    Fail-Max "Scientist status endpoint unavailable through frontend proxy." 41
}
$scientistProblem = Get-AuthorityProblem "scientist" $ScientistStatus
if ($null -ne $scientistProblem) {
    Fail-Max $scientistProblem 42
}

Write-Host "[READY] Backend = READY"
Write-Host ("[READY] SQLite = READY (schema " + [string]$Authority.schema_version + ")")
Write-Host ("[READY] EA v" + [string]$Authority.ea_version + " = " + [string]$Authority.baseline_status)
Write-Host ("[READY] Strategy epoch = " + [string]$Authority.strategy_contract)
Write-Host "[READY] Scientist knowledge = READY"
Write-Host ("[READY] Scientist provider = " + [string]$ScientistStatus.provider + " / " + [string]$ScientistStatus.model + " / " + [string]$ScientistStatus.provider_status)
$ChampionId = if ($null -eq $proxyOverview.current_strategy_champion) { "NONE" } else { [string]$proxyOverview.current_strategy_champion.strategy_id }
Write-Host "[READY] Strategy Champion = $ChampionId"
Write-Host "[READY] MT5 = READY_EXECUTABLE_AND_DATA_ROOT"

try {
    Start-Process $FrontendUrl
} catch {
    Fail-Max ("Application is ready but the default browser could not be opened: " + $_.Exception.Message) 50
}

Write-Host "[READY] Browser opened after readiness."
Write-Host "MAX_READY"
exit 0
