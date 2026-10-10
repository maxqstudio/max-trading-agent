param(
  [string]$Python = '',
  [string]$SkillWorkflowPath = ""
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Import-Module (Join-Path $PSScriptRoot 'onnx01_skill_workflow_cache.psm1') -Force

if ($Python) {
  $pythonPath = if ([System.IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $repoRoot $Python }
} else {
  $pythonCandidates = [System.Collections.Generic.List[string]]::new()
  $pythonCandidates.Add((Join-Path $repoRoot '.venv\Scripts\python.exe'))
  $commonGitDirectory = (& git -C $repoRoot rev-parse --git-common-dir).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve the repository Git common directory to locate the default Python environment.' }
  if (-not [System.IO.Path]::IsPathRooted($commonGitDirectory)) {
    $commonGitDirectory = Join-Path $repoRoot $commonGitDirectory
  }
  $commonRepositoryRoot = Split-Path ([System.IO.Path]::GetFullPath($commonGitDirectory)) -Parent
  $pythonCandidates.Add((Join-Path $commonRepositoryRoot '.venv\Scripts\python.exe'))
  $pythonPath = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
  if (-not $pythonPath) { $pythonPath = $pythonCandidates[0] }
}
$frontendRoot = Join-Path $repoRoot 'frontend'
$cacheRegressionScript = Join-Path $PSScriptRoot 'test_onnx01_skill_workflow_cache.ps1'
$evidenceRoot = Join-Path $repoRoot 'evidence\onnx01'
$evidencePath = Join-Path $evidenceRoot 'acceptance.json'
$skillSha = '964481ed1609f87904ba9e08890bffc0a10c3fd4'
$skillWorkflowUrl = 'https://github.com/maxqstudio/Skill_Workflow.git'
$startedAt = [DateTimeOffset]::UtcNow
$oldPythonPath = $env:PYTHONPATH
$oldPythonDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
$oldLocation = Get-Location
$gates = [System.Collections.Generic.List[object]]::new()
$firstFailedGate = $null
$script:skillWorkflowRootForRun = $null

function Invoke-Gate {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][scriptblock]$Action
  )

  $gateStart = [DateTimeOffset]::UtcNow
  $exitCode = 0
  $output = ''
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    # Windows PowerShell 5.1 promotes native stderr (including Vite's
    # non-fatal chunk-size warning) to ErrorRecord. Preserve that diagnostic
    # output, but determine gate success from the native process exit code.
    $ErrorActionPreference = 'Continue'
    $output = (& $Action 2>&1 | ForEach-Object { [string]$_ }) -join "`n"
    if ($null -ne $LASTEXITCODE) { $exitCode = [int]$LASTEXITCODE }
  } catch {
    $exitCode = 1
    $output = (($_ | Out-String).Trim())
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
  }

  $status = if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }
  $gates.Add([ordered]@{
    name = $Name
    command = $Command
    status = $status
    exit_code = $exitCode
    started_at_utc = $gateStart.ToString('o')
    duration_seconds = [Math]::Round(([DateTimeOffset]::UtcNow - $gateStart).TotalSeconds, 3)
    output = if ($output.Length -gt 6000) { $output.Substring($output.Length - 6000) } else { $output }
  })
  Write-Host ("[{0}] {1} (exit {2})" -f $status, $Name, $exitCode)
  if ($output) { Write-Host $output }
  return ($exitCode -eq 0)
}

try {
  Set-Location $repoRoot
  $env:PYTHONPATH = Join-Path $repoRoot 'backend'
  # Keep the pinned Skill Workflow checkout immutable across repeated runs.
  # Python self-tests execute from that checkout and otherwise may leave
  # untracked __pycache__ files that make the next cache identity check fail.
  $env:PYTHONDONTWRITEBYTECODE = '1'

  $head = (& git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve candidate HEAD.' }
  $tree = (& git rev-parse 'HEAD^{tree}').Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve candidate tree.' }
  $branch = (& git branch --show-current).Trim()
  $base = (& git rev-parse origin/main).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'origin/main is unavailable; fetch it before acceptance.' }

  $cleanGate = Invoke-Gate -Name 'CANDIDATE_CLEAN' -Command 'git status --porcelain' -Action {
    $dirty = (& git status --porcelain) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'git status failed.' }
    if ($dirty) { Write-Output $dirty; throw 'Candidate worktree must be clean so evidence binds to HEAD.' }
    Write-Output 'Candidate worktree is clean.'
  }
  if (-not $cleanGate) { $firstFailedGate = 'CANDIDATE_CLEAN' }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'SKILL_WORKFLOW_CACHE' -Command "resolve the worktree-local Skill Workflow cache at $skillSha before document scans" -Action {
      $script:skillWorkflowRootForRun = Resolve-Onnx01SkillWorkflowRoot `
        -RepositoryRoot $repoRoot `
        -SkillWorkflowSha $skillSha `
        -RepositoryUrl $skillWorkflowUrl `
        -SkillWorkflowPath $SkillWorkflowPath
      Write-Output "Pinned Skill Workflow checkout verified at $script:skillWorkflowRootForRun."
    }
    if (-not $g) { $firstFailedGate = 'SKILL_WORKFLOW_CACHE' }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'SKILL_CACHE_REGRESSION' -Command 'powershell -NoProfile -ExecutionPolicy Bypass -File scripts/test_onnx01_skill_workflow_cache.ps1' -Action {
      & powershell -NoProfile -ExecutionPolicy Bypass -File $cacheRegressionScript
      if ($LASTEXITCODE -ne 0) { throw "Skill cache regression exit $LASTEXITCODE" }
    }
    if (-not $g) { $firstFailedGate = 'SKILL_CACHE_REGRESSION' }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'DIFF_CHECK' -Command 'git diff --check origin/main...HEAD' -Action {
      & git diff --check origin/main...HEAD
      if ($LASTEXITCODE -ne 0) { throw 'Diff whitespace check failed.' }
    }
    if (-not $g) { $firstFailedGate = 'DIFF_CHECK' }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'BACKEND' -Command 'python -m pytest backend/tests -q -o addopts=' -Action {
      & $pythonPath -m pytest backend\tests -q -o addopts=
      if ($LASTEXITCODE -ne 0) { throw "Backend pytest exit $LASTEXITCODE" }
    }
    if (-not $g) { $firstFailedGate = 'BACKEND' }
  }
  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'PIP_CHECK' -Command 'python -m pip check' -Action {
      & $pythonPath -m pip check
      if ($LASTEXITCODE -ne 0) { throw "pip check exit $LASTEXITCODE" }
    }
    if (-not $g) { $firstFailedGate = 'PIP_CHECK' }
  }

  Push-Location $frontendRoot
  try {
    foreach ($frontendGate in @(
      @{ name = 'FRONTEND_TESTS'; command = 'npm test -- --run'; args = @('test', '--', '--run') },
      @{ name = 'FRONTEND_LINT'; command = 'npm run lint'; args = @('run', 'lint') },
      @{ name = 'FRONTEND_BUILD'; command = 'npm run build'; args = @('run', 'build') },
      @{ name = 'NPM_TREE'; command = 'npm ls --all'; args = @('ls', '--all') }
    )) {
      if ($firstFailedGate) { break }
      $argsCopy = $frontendGate.args
      $g = Invoke-Gate -Name $frontendGate.name -Command $frontendGate.command -Action {
        & npm @argsCopy
        if ($LASTEXITCODE -ne 0) { throw "npm exit $LASTEXITCODE" }
      }
      if (-not $g) { $firstFailedGate = $frontendGate.name }
    }
  } finally {
    Pop-Location
  }

  if (-not $firstFailedGate) {
    foreach ($validator in @(
      @{ name = 'PROJECT_DOCS'; path = '.workflow/tools/validate_project_docs.py'; args = @() },
      @{ name = 'DOC_QUALITY'; path = '.workflow/tools/validate_doc_quality.py'; args = @() },
      @{ name = 'SEQUENCE_SESSIONS'; path = '.workflow/tools/validate_sequence_sessions.py'; args = @() },
      @{ name = 'HANDOFF'; path = '.workflow/tools/validate_handoff.py'; args = @() },
      @{ name = 'HUMAN_COMPREHENSION'; path = '.workflow/tools/validate_human_comprehension.py'; args = @('--require-pass') },
      @{ name = 'CROSS_DOCUMENT'; path = '.workflow/tools/validate_cross_document_consistency.py'; args = @() },
      @{ name = 'PROJECT_TRUTH'; path = '.workflow/tools/validate_project_truth.py'; args = @() }
    )) {
      if ($firstFailedGate) { break }
      $validatorPath = Join-Path $repoRoot $validator.path
      $validatorArgs = $validator.args
      $g = Invoke-Gate -Name $validator.name -Command ("python " + $validator.path + ' ' + ($validator.args -join ' ')).Trim() -Action {
        & $pythonPath $validatorPath @validatorArgs
        if ($LASTEXITCODE -ne 0) { throw "$($validator.name) exit $LASTEXITCODE" }
      }
      if (-not $g) { $firstFailedGate = $validator.name }
    }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'SKILL_WORKFLOW_PROVENANCE' -Command 'verify pinned Skill Workflow tools at 964481ed1609f87904ba9e08890bffc0a10c3fd4' -Action {
      $skillRoot = $script:skillWorkflowRootForRun
      if (-not $skillRoot) { throw 'The verified Skill Workflow cache was not resolved before governance validation.' }
      $upstream = @(Get-ChildItem (Join-Path $skillRoot 'scripts\*.py') | Where-Object { $_.Name -notin @('initialize_project_truth.py', 'selftest_project_truth_compiler.py') })
      if ($upstream.Count -ne 21) { throw "Unexpected pinned Skill Workflow tool count: $($upstream.Count)" }
      foreach ($file in $upstream) {
        $localPath = Join-Path $repoRoot (Join-Path '.workflow\tools' $file.Name)
        if (-not (Test-Path $localPath)) { throw "Missing vendored tool $($file.Name)" }
        $upstreamBlob = (& git -C $skillRoot rev-parse "${skillSha}:scripts/$($file.Name)").Trim()
        $localBlob = (& git hash-object $localPath).Trim()
        if ($LASTEXITCODE -ne 0 -or $upstreamBlob -ne $localBlob) { throw "Pinned provenance mismatch: $($file.Name)" }
      }
      Write-Output "21 tools match Skill_Workflow $skillSha."
    }
    if (-not $g) { $firstFailedGate = 'SKILL_WORKFLOW_PROVENANCE' }
  }

  if (-not $firstFailedGate) {
    $skillRoot = $script:skillWorkflowRootForRun
    foreach ($skillTest in @(
      @{ name = 'STRICT_SELFTEST'; script = 'selftest_strict_project_workflow.py' },
      @{ name = 'PROJECT_TRUTH_COMPILER_SELFTEST'; script = 'selftest_project_truth_compiler.py' },
      @{ name = 'SEQUENCE_CALL_REGRESSION'; script = 'selftest_sequence_call_resolution.py' },
      @{ name = 'CROSS_DOCUMENT_REGRESSIONS'; script = 'selftest_cross_document_regressions.py' }
    )) {
      if ($firstFailedGate) { break }
      $skillScript = Join-Path $skillRoot (Join-Path 'scripts' $skillTest.script)
      $g = Invoke-Gate -Name $skillTest.name -Command ("python " + $skillScript) -Action {
        & $pythonPath $skillScript
        if ($LASTEXITCODE -ne 0) { throw "$($skillTest.name) exit $LASTEXITCODE" }
      }
      if (-not $g) { $firstFailedGate = $skillTest.name }
    }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'SOURCE_ONLY' -Command "python scripts/scan_m05_candidate_tree.py --candidate-sha $head" -Action {
      & $pythonPath scripts\scan_m05_candidate_tree.py --candidate-sha $head
      if ($LASTEXITCODE -ne 0) { throw "Source-only scan exit $LASTEXITCODE" }
    }
    if (-not $g) { $firstFailedGate = 'SOURCE_ONLY' }
  }

  if (-not $firstFailedGate) {
    $g = Invoke-Gate -Name 'FINAL_TREE_IDENTITY' -Command 'verify HEAD and tree unchanged and clean' -Action {
      $endHead = (& git rev-parse HEAD).Trim()
      $endTree = (& git rev-parse 'HEAD^{tree}').Trim()
      $dirty = (& git status --porcelain) -join "`n"
      if ($LASTEXITCODE -ne 0 -or $endHead -ne $head -or $endTree -ne $tree -or $dirty) {
        throw 'Candidate identity changed or tracked worktree became dirty during acceptance.'
      }
      Write-Output "HEAD=$endHead TREE=$endTree"
    }
    if (-not $g) { $firstFailedGate = 'FINAL_TREE_IDENTITY' }
  }
} catch {
  if (-not $firstFailedGate) { $firstFailedGate = 'RUNNER_SETUP' }
  $gates.Add([ordered]@{
    name = $firstFailedGate
    command = 'acceptance runner setup or gate orchestration'
    status = 'FAIL'
    exit_code = 1
    started_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    duration_seconds = 0
    output = (($_ | Out-String).Trim())
  })
  Write-Host (($_ | Out-String).Trim())
} finally {
  if ($null -eq $oldPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue }
  else { $env:PYTHONPATH = $oldPythonPath }
  if ($null -eq $oldPythonDontWriteBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue }
  else { $env:PYTHONDONTWRITEBYTECODE = $oldPythonDontWriteBytecode }
  Set-Location $oldLocation
}

New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
$report = [ordered]@{
  schema_version = 1
  phase = 'ONNX-01'
  repository = 'maxqstudio/max-trading-agent'
  branch = $branch
  base_sha = $base
  candidate_sha = $head
  candidate_tree_sha = $tree
  skill_workflow_sha = $skillSha
  started_at_utc = $startedAt.ToString('o')
  finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
  status = if ($firstFailedGate) { 'FAIL' } else { 'PASS' }
  first_failed_gate = $firstFailedGate
  gates = @($gates.ToArray())
  runtime_boundary = 'No Owner PC, MT5, dataset intake, scientific execution, model training/scoring, ONNX export, or ONNX runtime was performed.'
}
$json = $report | ConvertTo-Json -Depth 10
$timestamp = $startedAt.UtcDateTime.ToString('yyyyMMddTHHmmss.fffZ')
$timestampedEvidencePath = Join-Path $evidenceRoot ("acceptance-$timestamp.json")
if (Test-Path -LiteralPath $timestampedEvidencePath) {
  $timestampedEvidencePath = Join-Path $evidenceRoot ("acceptance-$timestamp-" + [Guid]::NewGuid().ToString('N') + '.json')
}
$evidenceContent = $json + [Environment]::NewLine
[System.IO.File]::WriteAllText($timestampedEvidencePath, $evidenceContent, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($evidencePath, $evidenceContent, [System.Text.UTF8Encoding]::new($false))
$failedGateLabel = if ($firstFailedGate) { $firstFailedGate } else { 'NONE' }
Write-Host ("ACCEPTANCE={0}; FIRST_FAILED_GATE={1}; EVIDENCE={2}; TIMESTAMPED_EVIDENCE={3}" -f $report.status, $failedGateLabel, $evidencePath, $timestampedEvidencePath)
if ($firstFailedGate) { exit 1 }
exit 0
