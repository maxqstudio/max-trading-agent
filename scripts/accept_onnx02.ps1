param(
  [string]$Python = '',
  [string]$SkillWorkflowPath = ''
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$pythonPath = if ($Python) {
  if ([System.IO.Path]::IsPathRooted($Python)) { $Python } else { Join-Path $repoRoot $Python }
} else {
  $commonGitDirectory = (& git -C $repoRoot rev-parse --git-common-dir).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve Git common directory.' }
  if (-not [System.IO.Path]::IsPathRooted($commonGitDirectory)) { $commonGitDirectory = Join-Path $repoRoot $commonGitDirectory }
  $commonRepositoryRoot = Split-Path ([System.IO.Path]::GetFullPath($commonGitDirectory)) -Parent
  $candidate = Join-Path $commonRepositoryRoot '.venv\Scripts\python.exe'
  if (Test-Path -LiteralPath $candidate -PathType Leaf) { $candidate } else { Join-Path $repoRoot '.venv\Scripts\python.exe' }
}
$frontendRoot = Join-Path $repoRoot 'frontend'
$evidenceRoot = Join-Path $repoRoot 'evidence\onnx02'
$evidencePath = Join-Path $evidenceRoot 'acceptance.json'
$baseSha = '1a9c9a6990e1f2571e4d85d79436402e6d37ec23'
$startedAt = [DateTimeOffset]::UtcNow
$oldLocation = Get-Location
$oldPythonPath = $env:PYTHONPATH
$oldBytecode = $env:PYTHONDONTWRITEBYTECODE
$gates = [System.Collections.Generic.List[object]]::new()
$firstFailedGate = $null

function Invoke-Gate {
  param([string]$Name, [string]$Command, [scriptblock]$Action)
  $begin = [DateTimeOffset]::UtcNow
  $exitCode = 0
  $output = ''
  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = (& $Action 2>&1 | ForEach-Object { [string]$_ }) -join "`n"
    if ($null -ne $LASTEXITCODE) { $exitCode = [int]$LASTEXITCODE }
  } catch {
    $exitCode = 1
    $exceptionOutput = (($_ | Out-String).Trim())
    $output = if ($output) { $output + "`n" + $exceptionOutput } else { $exceptionOutput }
  } finally {
    $ErrorActionPreference = $previousPreference
  }
  $gates.Add([ordered]@{
    name = $Name
    command = $Command
    status = if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }
    exit_code = $exitCode
    started_at_utc = $begin.ToString('o')
    duration_seconds = [Math]::Round(([DateTimeOffset]::UtcNow - $begin).TotalSeconds, 3)
    output = if ($output.Length -gt 6000) { $output.Substring($output.Length - 6000) } else { $output }
  })
  Write-Host ("[{0}] {1} exit={2}" -f $(if ($exitCode -eq 0) { 'PASS' } else { 'FAIL' }), $Name, $exitCode)
  if ($output) { Write-Host $output }
  return ($exitCode -eq 0)
}

try {
  Set-Location $repoRoot
  $env:PYTHONPATH = Join-Path $repoRoot 'backend'
  $env:PYTHONDONTWRITEBYTECODE = '1'
  $head = (& git rev-parse HEAD).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve candidate HEAD.' }
  $tree = (& git rev-parse 'HEAD^{tree}').Trim()
  if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve candidate tree.' }
  $branch = (& git branch --show-current).Trim()
  $observedBase = (& git rev-parse origin/main).Trim()
  if ($LASTEXITCODE -ne 0) { throw 'origin/main is unavailable; fetch before running acceptance.' }

  $ok = Invoke-Gate -Name 'CANDIDATE_IDENTITY' -Command 'verify expected branch, accepted parent and clean committed tree' -Action {
    $dirty = (& git status --porcelain --untracked-files=all) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw 'git status failed.' }
    if ($branch -ne 'codex/onnx-02-data-intake') { throw "Unexpected candidate branch: $branch" }
    if ($observedBase -ne $baseSha) { throw "origin/main advanced from the accepted parent: $observedBase" }
    if ($dirty) { throw "Candidate must be clean before acceptance: $dirty" }
    if ($head -eq $baseSha) { throw 'Candidate HEAD must contain the ONNX-02 implementation.' }
    Write-Output "BRANCH=$branch HEAD=$head TREE=$tree BASE=$observedBase"
  }
  if (-not $ok) { $firstFailedGate = 'CANDIDATE_IDENTITY' }

  foreach ($iteration in 1..2) {
    if ($firstFailedGate) { break }
    $name = "BACKEND_ONNX02_FOCUSED_REPEAT_$iteration"
    $ok = Invoke-Gate -Name $name -Command '.venv/Scripts/python.exe -m pytest backend/tests/test_onnx_data_intake.py backend/tests/test_onnx_data_api.py -q -o addopts=' -Action {
      & $pythonPath -m pytest backend\tests\test_onnx_data_intake.py backend\tests\test_onnx_data_api.py -q -o addopts=
      if ($LASTEXITCODE -ne 0) { throw "Focused backend test exit $LASTEXITCODE" }
    }
    if (-not $ok) { $firstFailedGate = $name }
  }

  foreach ($iteration in 1..2) {
    if ($firstFailedGate) { break }
    $name = "FRONTEND_ONNX02_FOCUSED_REPEAT_$iteration"
    $ok = Invoke-Gate -Name $name -Command 'npm exec -- vitest run src/onnxDataApi.test.ts src/OnnxDataIntake.test.tsx --pool=forks --maxWorkers=1' -Action {
      Push-Location $frontendRoot
      try {
        & npm exec -- vitest run src/onnxDataApi.test.ts src/OnnxDataIntake.test.tsx --pool=forks --maxWorkers=1
        if ($LASTEXITCODE -ne 0) { throw "Focused frontend test exit $LASTEXITCODE" }
      } finally { Pop-Location }
    }
    if (-not $ok) { $firstFailedGate = $name }
  }

  if (-not $firstFailedGate) {
    $ok = Invoke-Gate -Name 'SCIENTIST_KNOWLEDGE_MANIFEST' -Command 'python -c "from max_backend.scientist_knowledge import load_knowledge; load_knowledge()"' -Action {
      Write-Output "PYTHON=$pythonPath; PYTHONPATH=$env:PYTHONPATH; ROOT=$((Get-Location).Path)"
      & $pythonPath -c 'import sys; from pathlib import Path; from max_backend.scientist_knowledge import load_knowledge; snapshot = load_knowledge(root=Path.cwd()); print("PYTHON=" + sys.executable); print("SCIENTIST_KNOWLEDGE=VERIFIED; SOURCE_COUNT=" + str(len(snapshot["source_manifest"])))'
      $pythonExit = $LASTEXITCODE
      if ($pythonExit -ne 0) { throw "Scientist manifest verification exit $pythonExit" }
    }
    if (-not $ok) { $firstFailedGate = 'SCIENTIST_KNOWLEDGE_MANIFEST' }
  }

  if (-not $firstFailedGate) {
    $ok = Invoke-Gate -Name 'CUMULATIVE_ACCEPTANCE' -Command 'scripts/accept_onnx01.ps1 with the current pinned Skill Workflow and complete repository gates' -Action {
      $runner = Join-Path $PSScriptRoot 'accept_onnx01.ps1'
      & powershell -NoProfile -ExecutionPolicy Bypass -File $runner -Python $pythonPath -SkillWorkflowPath $SkillWorkflowPath
      if ($LASTEXITCODE -ne 0) { throw "Cumulative repository acceptance exit $LASTEXITCODE" }
      $childPath = Join-Path $repoRoot 'evidence\onnx01\acceptance.json'
      if (-not (Test-Path -LiteralPath $childPath -PathType Leaf)) { throw 'Cumulative acceptance evidence is missing.' }
      $child = Get-Content -Raw $childPath | ConvertFrom-Json
      if ($child.status -ne 'PASS' -or $child.first_failed_gate) { throw 'Cumulative acceptance report did not record a complete PASS.' }
      if ($child.candidate_sha -ne $head -or $child.candidate_tree_sha -ne $tree) { throw 'Cumulative acceptance evidence does not bind to the ONNX-02 candidate identity.' }
      Write-Output "CUMULATIVE_STATUS=$($child.status); CHILD_EVIDENCE=$childPath"
    }
    if (-not $ok) { $firstFailedGate = 'CUMULATIVE_ACCEPTANCE' }
  }

  if (-not $firstFailedGate) {
    $ok = Invoke-Gate -Name 'FINAL_CANDIDATE_IDENTITY' -Command 'verify HEAD, tree and clean worktree unchanged' -Action {
      $endHead = (& git rev-parse HEAD).Trim()
      $endTree = (& git rev-parse 'HEAD^{tree}').Trim()
      $dirty = (& git status --porcelain --untracked-files=all) -join "`n"
      if ($LASTEXITCODE -ne 0 -or $endHead -ne $head -or $endTree -ne $tree -or $dirty) { throw 'Candidate changed or is dirty during acceptance.' }
      Write-Output "HEAD=$endHead TREE=$endTree"
    }
    if (-not $ok) { $firstFailedGate = 'FINAL_CANDIDATE_IDENTITY' }
  }
} catch {
  if (-not $firstFailedGate) { $firstFailedGate = 'RUNNER_SETUP' }
  $gates.Add([ordered]@{
    name = $firstFailedGate
    command = 'acceptance runner setup/orchestration'
    status = 'FAIL'
    exit_code = 1
    started_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
    duration_seconds = 0
    output = (($_ | Out-String).Trim())
  })
  Write-Host (($_ | Out-String).Trim())
} finally {
  if ($null -eq $oldPythonPath) { Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue } else { $env:PYTHONPATH = $oldPythonPath }
  if ($null -eq $oldBytecode) { Remove-Item Env:PYTHONDONTWRITEBYTECODE -ErrorAction SilentlyContinue } else { $env:PYTHONDONTWRITEBYTECODE = $oldBytecode }
  Set-Location $oldLocation
}

New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
$report = [ordered]@{
  schema_version = 1
  phase = 'ONNX-02'
  repository = 'maxqstudio/max-trading-agent'
  branch = $branch
  base_sha = $observedBase
  candidate_sha = $head
  candidate_tree_sha = $tree
  skill_workflow_sha = '964481ed1609f87904ba9e08890bffc0a10c3fd4'
  started_at_utc = $startedAt.ToString('o')
  finished_at_utc = [DateTimeOffset]::UtcNow.ToString('o')
  status = if ($firstFailedGate) { 'FAIL' } else { 'PASS' }
  first_failed_gate = if ($firstFailedGate) { $firstFailedGate } else { 'NONE' }
  gates = @($gates.ToArray())
  evidence_boundary = 'Synthetic-only repository acceptance; no Owner PC, real data, broker reconciliation, MT5, scientific execution, training, ONNX export/runtime, or Champion mutation.'
}
$json = $report | ConvertTo-Json -Depth 10
$timestamp = $startedAt.UtcDateTime.ToString('yyyyMMddTHHmmss.fffZ')
$timestampedPath = Join-Path $evidenceRoot ("acceptance-$timestamp.json")
if (Test-Path -LiteralPath $timestampedPath) { $timestampedPath = Join-Path $evidenceRoot ("acceptance-$timestamp-" + [Guid]::NewGuid().ToString('N') + '.json') }
$content = $json + [Environment]::NewLine
[System.IO.File]::WriteAllText($timestampedPath, $content, [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText($evidencePath, $content, [System.Text.UTF8Encoding]::new($false))
Write-Host ("ACCEPTANCE={0}; FIRST_FAILED_GATE={1}; EVIDENCE={2}; TIMESTAMPED_EVIDENCE={3}" -f $report.status, $report.first_failed_gate, $evidencePath, $timestampedPath)
if ($firstFailedGate) { exit 1 }
exit 0
