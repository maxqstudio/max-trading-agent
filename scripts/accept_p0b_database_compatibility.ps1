[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$canonicalRoot = [System.IO.Path]::GetFullPath('D:\max')
$rootPrefix = $canonicalRoot.TrimEnd('\') + '\'
if (-not $repoRoot.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "P0B acceptance must run inside D:\max; resolved root: $repoRoot"
}

Set-Location $repoRoot
$pythonCandidates = @(
    (Join-Path $repoRoot '.venv\Scripts\python.exe'),
    (Join-Path $canonicalRoot '.venv\Scripts\python.exe')
)
$python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $python) {
    throw "P0B acceptance Python environment is missing from the worktree and D:\max: $($pythonCandidates -join ', ')"
}
$git = (Get-Command git.exe -ErrorAction Stop).Source
$cmd = Join-Path $env:SystemRoot 'System32\cmd.exe'
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$runRoot = Join-Path $repoRoot (Join-Path 'artifacts\p0b\runs' $runId)
New-Item -ItemType Directory -Path $runRoot -ErrorAction Stop | Out-Null
$env:TEMP = $runRoot
$env:TMP = $runRoot
$env:PYTHONPATH = Join-Path $repoRoot 'backend'

$script:result = [ordered]@{
    schema = 'MAX_P0B_ACCEPTANCE_V1'
    status = 'RUNNING'
    first_failed_gate = $null
    started_utc = (Get-Date).ToUniversalTime().ToString('o')
    repository_root = $repoRoot
    branch = $null
    head_sha = $null
    head_tree_sha = $null
    owner_database_accessed = $false
    gates = @()
    evidence_directory = $runRoot
}
$reportPath = Join-Path $runRoot 'result.json'

function Save-Result {
    $temporary = $reportPath + '.tmp'
    $json = $script:result | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText(
        $temporary,
        $json + [Environment]::NewLine,
        (New-Object System.Text.UTF8Encoding($false))
    )
    Move-Item -LiteralPath $temporary -Destination $reportPath -Force
}

function Invoke-Gate {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory
    )
    $stdoutPath = Join-Path $runRoot ($Name + '.stdout.log')
    $stderrPath = Join-Path $runRoot ($Name + '.stderr.log')
    $started = (Get-Date).ToUniversalTime().ToString('o')
    $exitCode = 9009
    $errorText = $null
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -PassThru -Wait -NoNewWindow `
            -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
        $exitCode = [int]$process.ExitCode
    }
    catch {
        $errorText = $_.Exception.Message
        [System.IO.File]::WriteAllText($stderrPath, $errorText, (New-Object System.Text.UTF8Encoding($false)))
    }
    $entry = [ordered]@{
        gate = $Name
        status = $(if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' })
        exit_code = $exitCode
        command = @($FilePath) + $Arguments
        working_directory = $WorkingDirectory
        started_utc = $started
        completed_utc = (Get-Date).ToUniversalTime().ToString('o')
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        error = $errorText
    }
    $script:result.gates += $entry
    if ($exitCode -ne 0 -and -not $script:result.first_failed_gate) {
        $script:result.first_failed_gate = $Name
    }
    Save-Result
    Write-Host ("{0}={1} (exit {2})" -f $Name, $entry.status, $exitCode)
    return ($exitCode -eq 0)
}

function Invoke-InternalGate {
    param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][scriptblock]$Action)
    $stdoutPath = Join-Path $runRoot ($Name + '.stdout.log')
    $stderrPath = Join-Path $runRoot ($Name + '.stderr.log')
    $started = (Get-Date).ToUniversalTime().ToString('o')
    $exitCode = 0
    $errorText = $null
    try {
        $output = @(& $Action 2>&1 | ForEach-Object { $_.ToString() })
        [System.IO.File]::WriteAllLines($stdoutPath, $output, (New-Object System.Text.UTF8Encoding($false)))
        [System.IO.File]::WriteAllText($stderrPath, '', (New-Object System.Text.UTF8Encoding($false)))
    }
    catch {
        $exitCode = 1
        $errorText = $_.Exception.Message
        [System.IO.File]::WriteAllText($stderrPath, $errorText, (New-Object System.Text.UTF8Encoding($false)))
    }
    $entry = [ordered]@{
        gate = $Name
        status = $(if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' })
        exit_code = $exitCode
        command = 'PowerShell acceptance assertion'
        working_directory = $repoRoot
        started_utc = $started
        completed_utc = (Get-Date).ToUniversalTime().ToString('o')
        stdout_log = $stdoutPath
        stderr_log = $stderrPath
        error = $errorText
    }
    $script:result.gates += $entry
    if ($exitCode -ne 0 -and -not $script:result.first_failed_gate) {
        $script:result.first_failed_gate = $Name
    }
    Save-Result
    Write-Host ("{0}={1}" -f $Name, $entry.status)
    return ($exitCode -eq 0)
}

function Invoke-PythonGate {
    param([string]$Name, [string[]]$Arguments, [string]$WorkingDirectory = $repoRoot)
    return Invoke-Gate -Name $Name -FilePath $python -Arguments $Arguments -WorkingDirectory $WorkingDirectory
}

function Invoke-NpmGate {
    param([string]$Name, [string]$CommandText)
    return Invoke-Gate -Name $Name -FilePath $cmd -Arguments @('/d', '/s', '/c', $CommandText) -WorkingDirectory (Join-Path $repoRoot 'frontend')
}

try {
    $script:result.branch = (& $git -C $repoRoot branch --show-current).Trim()
    $script:result.head_sha = (& $git -C $repoRoot rev-parse HEAD).Trim()
    $script:result.head_tree_sha = (& $git -C $repoRoot rev-parse 'HEAD^{tree}').Trim()
    Save-Result

    if (-not (Invoke-InternalGate 'CANDIDATE_IDENTITY' {
        if ($script:result.branch -ne 'codex/p0b-schema16-compat') { throw "Unexpected branch: $($script:result.branch)" }
        $main = (& $git -C $repoRoot rev-parse origin/main).Trim()
        $base = (& $git -C $repoRoot merge-base HEAD origin/main).Trim()
        if ($base -ne $main) { throw "Candidate is not based on current origin/main: base=$base main=$main" }
        $status = @(& $git -C $repoRoot status --porcelain=v1)
        if ($status.Count -gt 0) { throw 'Worktree must be clean before acceptance.' }
        $diffCheck = & $git -C $repoRoot diff --check origin/main...HEAD 2>&1
        if ($LASTEXITCODE -ne 0) { throw ($diffCheck | Out-String) }
        "branch=$($script:result.branch)"
        "head=$($script:result.head_sha)"
        "tree=$($script:result.head_tree_sha)"
        "base=$main"
    })) { throw 'CANDIDATE_IDENTITY failed' }

    $trackedPolicy = {
        $tracked = @(& $git -C $repoRoot ls-files)
        $bad = @($tracked | Where-Object {
            (($_ -match '(^|/)\.env($|\.)') -and ($_ -notmatch '(^|/)\.env\.example$')) -or
            $_ -match '^evidence/' -or $_ -match '^artifacts/' -or
            $_ -match '^state/.*\.(db|sqlite|sqlite3)(-|$)' -or
            $_ -match '(^|/)\.vite/' -or $_ -match '(^|/)\.venv/' -or
            $_ -match '^frontend/node_modules/' -or $_ -match '^frontend/dist/' -or
            $_ -match '\.(pem|pfx|p12|key)$'
        })
        if ($bad.Count -gt 0) { throw ('Forbidden tracked paths: ' + ($bad -join ', ')) }
        "tracked_files=$($tracked.Count)"
        'forbidden_paths=0'
    }
    if (-not (Invoke-InternalGate 'TRACKED_SOURCE_POLICY' $trackedPolicy)) { throw 'TRACKED_SOURCE_POLICY failed' }

    $skillDir = Join-Path $runRoot 'Skill_Workflow'
    if (-not (Invoke-Gate 'SKILL_WORKFLOW_CLONE' $git @('clone', '--quiet', 'https://github.com/maxqstudio/Skill_Workflow.git', $skillDir) $repoRoot)) { throw 'SKILL_WORKFLOW_CLONE failed' }
    $skillSha = '964481ed1609f87904ba9e08890bffc0a10c3fd4'
    if (-not (Invoke-Gate 'SKILL_WORKFLOW_CHECKOUT' $git @('-C', $skillDir, 'checkout', '--quiet', $skillSha) $repoRoot)) { throw 'SKILL_WORKFLOW_CHECKOUT failed' }
    $env:SKILL_WORKFLOW_DIR = $skillDir

    $provenance = {
        $exclude = @('initialize_project_truth.py', 'selftest_project_truth_compiler.py')
        $files = @(Get-ChildItem (Join-Path $skillDir 'scripts\*.py') | Where-Object { $_.Name -notin $exclude })
        if ($files.Count -ne 21) { throw "Unexpected pinned Skill Workflow tool count: $($files.Count)" }
        foreach ($file in $files) {
            $upstreamBlob = (& $git -C $skillDir rev-parse ("${skillSha}:scripts/$($file.Name)")).Trim()
            if ($LASTEXITCODE -ne 0) { throw "Cannot resolve upstream tool blob $($file.Name)" }
            $localBlob = (& $git -C $repoRoot rev-parse ("HEAD:.workflow/tools/$($file.Name)")).Trim()
            if ($LASTEXITCODE -ne 0 -or $upstreamBlob -ne $localBlob) { throw "Skill Workflow provenance mismatch: $($file.Name)" }
        }
        "skill_sha=$skillSha"
        "verified_tool_count=$($files.Count)"
    }
    if (-not (Invoke-InternalGate 'SKILL_WORKFLOW_PROVENANCE' $provenance)) { throw 'SKILL_WORKFLOW_PROVENANCE failed' }

    $pytestFocused = Join-Path $runRoot 'pytest-focused'
    New-Item -ItemType Directory -Path $pytestFocused | Out-Null
    if (-not (Invoke-PythonGate 'FOCUSED_DATABASE_COMPATIBILITY' @('-m', 'pytest', 'backend\tests\test_schema_compatibility_guard.py', '-q', '-o', 'addopts=', '--basetemp', $pytestFocused))) { throw 'FOCUSED_DATABASE_COMPATIBILITY failed' }

    $pytestFull = Join-Path $runRoot 'pytest-full'
    New-Item -ItemType Directory -Path $pytestFull | Out-Null
    if (-not (Invoke-PythonGate 'BACKEND_FULL' @('-m', 'pytest', 'backend\tests', '-q', '-o', 'addopts=', '--basetemp', $pytestFull))) { throw 'BACKEND_FULL failed' }
    if (-not (Invoke-PythonGate 'PIP_CHECK' @('-m', 'pip', 'check'))) { throw 'PIP_CHECK failed' }

    if (-not (Invoke-NpmGate 'FRONTEND_INSTALL' 'npm ci')) { throw 'FRONTEND_INSTALL failed' }
    if (-not (Invoke-NpmGate 'FRONTEND_TEST' 'npm test -- --run')) { throw 'FRONTEND_TEST failed' }
    if (-not (Invoke-NpmGate 'FRONTEND_LINT' 'npm run lint')) { throw 'FRONTEND_LINT failed' }
    if (-not (Invoke-NpmGate 'FRONTEND_BUILD' 'npm run build')) { throw 'FRONTEND_BUILD failed' }
    if (-not (Invoke-NpmGate 'NPM_TREE' 'npm ls --all')) { throw 'NPM_TREE failed' }

    $governance = @(
        @('PROJECT_TRUTH_SYNC', @('.workflow\tools\sync_project_truth.py', '--no-record')),
        @('PROJECT_DOCS', @('.workflow\tools\validate_project_docs.py')),
        @('DOC_QUALITY', @('.workflow\tools\validate_doc_quality.py')),
        @('SEQUENCE', @('.workflow\tools\validate_sequence_sessions.py')),
        @('HANDOFF', @('.workflow\tools\validate_handoff.py')),
        @('HUMAN_COMPREHENSION', @('.workflow\tools\validate_human_comprehension.py', '--require-pass')),
        @('CROSS_DOCUMENT', @('.workflow\tools\validate_cross_document_consistency.py')),
        @('PROJECT_TRUTH', @('.workflow\tools\validate_project_truth.py'))
    )
    foreach ($gate in $governance) {
        if (-not (Invoke-PythonGate $gate[0] $gate[1])) { throw "$($gate[0]) failed" }
    }

    $skillTests = @(
        @('STRICT_SELFTEST', (Join-Path $skillDir 'scripts\selftest_strict_project_workflow.py')),
        @('PROJECT_TRUTH_COMPILER_SELFTEST', (Join-Path $skillDir 'scripts\selftest_project_truth_compiler.py')),
        @('SEQUENCE_CALL_RESOLUTION', (Join-Path $skillDir 'scripts\selftest_sequence_call_resolution.py')),
        @('CROSS_DOCUMENT_REGRESSIONS', (Join-Path $skillDir 'scripts\selftest_cross_document_regressions.py'))
    )
    foreach ($gate in $skillTests) {
        if (-not (Invoke-PythonGate $gate[0] @($gate[1]))) { throw "$($gate[0]) failed" }
    }

    $scanReport = Join-Path $runRoot 'source-only-secret-scan.json'
    if (-not (Invoke-PythonGate 'SOURCE_ONLY_SECRET_SCAN' @('scripts\scan_m05_candidate_tree.py', '--candidate-sha', 'HEAD', '--output', $scanReport))) { throw 'SOURCE_ONLY_SECRET_SCAN failed' }
    if (-not (Invoke-InternalGate 'FINAL_TREE_CLEAN' {
        $status = @(& $git -C $repoRoot status --porcelain=v1)
        if ($status.Count -gt 0) { throw ('Acceptance modified tracked/unignored files: ' + ($status -join '; ')) }
        'worktree_clean=true'
    })) { throw 'FINAL_TREE_CLEAN failed' }

    $script:result.status = 'PASS'
    $script:result.first_failed_gate = 'NONE'
}
catch {
    if (-not $script:result.first_failed_gate) { $script:result.first_failed_gate = 'RUNNER_ABORT' }
    $script:result.status = 'FAIL'
    $script:result.runner_error = $_.Exception.Message
}
finally {
    $script:result.completed_utc = (Get-Date).ToUniversalTime().ToString('o')
    Save-Result
}

Write-Host "P0B_ACCEPTANCE_STATUS=$($script:result.status)"
Write-Host "FIRST_FAILED_GATE=$($script:result.first_failed_gate)"
Write-Host "EVIDENCE_JSON=$reportPath"
if ($script:result.status -ne 'PASS') { exit 1 }
exit 0
