param(
    [switch]$SkipFrontend
)

$ErrorActionPreference = "Stop"

if ($env:OS -ne "Windows_NT") {
    throw "MAX Trading Agent is Windows-only because the production runtime integrates with MetaTrader 5."
}

Set-Location $PSScriptRoot

$python = $null
foreach ($candidate in @(
    @{ Command = "py"; Args = @("-3.13") },
    @{ Command = "py"; Args = @("-3.12") },
    @{ Command = "python"; Args = @() }
)) {
    try {
        & $candidate.Command @($candidate.Args) --version *> $null
        if ($LASTEXITCODE -eq 0) {
            $python = $candidate
            break
        }
    } catch {}
}

if ($null -eq $python) {
    throw "Python 3.12+ was not found. Install Python for Windows, then rerun INSTALL.ps1."
}
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    & $python.Command @($python.Args) -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    throw "pip upgrade failed."
}
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) {
    throw "Python dependency installation failed."
}

& "$PSScriptRoot\scripts\install_r02_gpu_native_deps.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "R02 GPU native dependency installation failed."
}

if (-not $SkipFrontend) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm was not found. Install Node.js for Windows, then rerun INSTALL.ps1."
    }

    Push-Location frontend
    try {
        npm ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci failed."
        }
    } finally {
        Pop-Location
    }
}

Write-Host "INSTALL=PASS"
Write-Host "MetaTrader 5 is required for final runtime/E2E acceptance."
