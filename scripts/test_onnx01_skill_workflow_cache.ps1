$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$modulePath = Join-Path $PSScriptRoot 'onnx01_skill_workflow_cache.psm1'

if (-not (Test-Path -LiteralPath $modulePath -PathType Leaf)) {
  throw "Required cache helper is missing: $modulePath"
}

Import-Module $modulePath -Force

function Assert-True {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw $Message }
}

function Invoke-TestGit {
  param([string]$WorkingDirectory, [string[]]$GitArguments)
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = @(& git -C $WorkingDirectory @GitArguments 2>&1)
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldPreference
  }
  if ($exitCode -ne 0) {
    throw "git $($GitArguments -join ' ') failed ($exitCode): $($output -join ' ')"
  }
  return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Invoke-TestGitAtPath {
  param([string]$Path, [string[]]$GitArguments)
  $oldPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    $output = @(& git -C $Path @GitArguments 2>&1)
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $oldPreference
  }
  if ($exitCode -ne 0) {
    throw "git $($GitArguments -join ' ') failed ($exitCode): $($output -join ' ')"
  }
  return (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
}

function Assert-Throws {
  param([string]$Name, [scriptblock]$Action, [string]$ExpectedMessage = '')
  try {
    & $Action
  } catch {
    $message = $_.Exception.Message
    if ($ExpectedMessage -and $message -notmatch [System.Text.RegularExpressions.Regex]::Escape($ExpectedMessage)) {
      throw "$Name failed closed with an unexpected diagnostic: $message"
    }
    Write-Host "[PASS] $Name rejected: $message"
    return
  }
  throw "Expected fail-closed rejection: $Name"
}

function Remove-OwnedOnnx01TestDirectory {
  param([Parameter(Mandatory = $true)][string]$Path)
  $root = [System.IO.Path]::GetFullPath($Path)
  $prefix = $root.TrimEnd('\') + '\'
  $extendedRoot = '\\?\' + $root
  $entries = @([System.IO.Directory]::EnumerateFileSystemEntries($extendedRoot, '*', [System.IO.SearchOption]::AllDirectories))
  foreach ($entry in $entries) {
    if (-not $entry.StartsWith($extendedRoot.TrimEnd('\') + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Refusing cleanup of an entry outside the owned test directory: $entry"
    }
    $attributes = [System.IO.File]::GetAttributes($entry)
    if ($attributes -band [System.IO.FileAttributes]::ReparsePoint) {
      throw "Refusing recursive cleanup through a reparse point in the owned test directory: $entry"
    }
    if ($attributes -band [System.IO.FileAttributes]::ReadOnly) {
      [System.IO.File]::SetAttributes($entry, ($attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
    }
  }
  $rootAttributes = [System.IO.File]::GetAttributes($extendedRoot)
  if ($rootAttributes -band [System.IO.FileAttributes]::ReadOnly) {
    [System.IO.File]::SetAttributes($extendedRoot, ($rootAttributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
  }
  [System.IO.Directory]::Delete($extendedRoot, $true)
}

$originalHead = Invoke-TestGit $repoRoot @('rev-parse', 'HEAD')
$originalTree = Invoke-TestGit $repoRoot @('rev-parse', 'HEAD^{tree}')
$originalStatus = Invoke-TestGit $repoRoot @('status', '--porcelain', '--untracked-files=all')
$commonGitDirectory = Invoke-TestGit $repoRoot @('rev-parse', '--git-common-dir')
if (-not [System.IO.Path]::IsPathRooted($commonGitDirectory)) {
  $commonGitDirectory = Join-Path $repoRoot $commonGitDirectory
}
$testStorageRoot = Join-Path ([System.IO.Path]::GetFullPath($commonGitDirectory)) 'onnx01-cache-tests'
$testRoot = Join-Path $testStorageRoot ('.t' + [Guid]::NewGuid().ToString('N').Substring(0, 8))
$testMarker = Join-Path $testRoot 'owned-by-test.txt'

try {
  $runnerSource = Get-Content -LiteralPath (Join-Path $repoRoot 'scripts\accept_onnx01.ps1') -Raw
  Assert-True ($runnerSource -match '(?m)^\s*\$env:PYTHONDONTWRITEBYTECODE\s*=\s*[''\"]1[''\"]\s*$') 'Acceptance runner must prevent Python bytecode from dirtying the verified Skill Workflow cache.'

  New-Item -ItemType Directory -Path $testRoot -Force | Out-Null
  [System.IO.File]::WriteAllText($testMarker, 'This isolated fixture is owned by test_onnx01_skill_workflow_cache.ps1.')
  $fixtureRepo = Join-Path $testRoot 'src'
  New-Item -ItemType Directory -Path $fixtureRepo -Force | Out-Null

  Invoke-TestGitAtPath $fixtureRepo @('init', '--quiet') | Out-Null
  Invoke-TestGit $fixtureRepo @('config', 'user.name', 'ONNX Cache Test') | Out-Null
  Invoke-TestGit $fixtureRepo @('config', 'user.email', 'onnx-cache-test@example.invalid') | Out-Null
  [System.IO.File]::WriteAllText((Join-Path $fixtureRepo 'README.md'), 'Pinned fixture A.')
  Invoke-TestGit $fixtureRepo @('add', '-f', 'README.md') | Out-Null
  Invoke-TestGit $fixtureRepo @('commit', '--quiet', '-m', 'fixture A') | Out-Null
  $pinA = Invoke-TestGit $fixtureRepo @('rev-parse', 'HEAD')

  [System.IO.File]::WriteAllText((Join-Path $fixtureRepo 'README.md'), 'Pinned fixture B.')
  Invoke-TestGit $fixtureRepo @('commit', '--quiet', '-am', 'fixture B') | Out-Null
  $pinB = Invoke-TestGit $fixtureRepo @('rev-parse', 'HEAD')

  $freshRoot = Join-Path $testRoot 'f'
  New-Item -ItemType Directory -Path $freshRoot -Force | Out-Null
  $first = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $freshRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
  $expectedCache = Join-Path $freshRoot (Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $pinA)
  Assert-True ($first -eq [System.IO.Path]::GetFullPath($expectedCache)) 'Fresh clone did not use the worktree-local ignored cache path.'
  Assert-True ((Invoke-TestGit $first @('rev-parse', 'HEAD')) -eq $pinA) 'Fresh clone did not check out the exact pin.'
  Assert-True (-not (Test-Path (Join-Path $freshRoot ("evidence\onnx01\Skill_Workflow-" + $pinA)))) 'Fresh clone recreated the legacy evidence cache.'
  Write-Host '[PASS] fresh checkout cloned and verified in the isolated default cache.'

  foreach ($runNumber in 2..3) {
    $reused = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $freshRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
    Assert-True ($reused -eq $first) "Run $runNumber did not reuse the verified cache."
    Assert-True ((Invoke-TestGit $reused @('status', '--porcelain', '--untracked-files=all')) -eq '') "Run $runNumber left a dirty cache."
    Write-Host "[PASS] repeated cache invocation $runNumber reused the exact pin."
  }

  $override = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $freshRoot -SkillWorkflowSha $pinB -RepositoryUrl $fixtureRepo -SkillWorkflowPath $fixtureRepo
  Assert-True ($override -eq [System.IO.Path]::GetFullPath($fixtureRepo)) 'Valid explicit override was not preserved.'
  Write-Host '[PASS] exact-SHA explicit override accepted.'

  Assert-Throws 'explicit override pin mismatch' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $freshRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo -SkillWorkflowPath $fixtureRepo | Out-Null
  } 'pin mismatch'

  $mismatchRoot = Join-Path $testRoot 'm'
  $mismatchPath = Join-Path $mismatchRoot (Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $pinA)
  New-Item -ItemType Directory -Path $mismatchRoot -Force | Out-Null
  New-Item -ItemType Directory -Path (Split-Path $mismatchPath -Parent) -Force | Out-Null
  Invoke-TestGitAtPath $mismatchRoot @('clone', '--quiet', $fixtureRepo, $mismatchPath) | Out-Null
  Assert-Throws 'cache HEAD mismatch' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $mismatchRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo | Out-Null
  } 'pin mismatch'

  Assert-Throws 'cache origin mismatch' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $freshRoot -SkillWorkflowSha $pinA -RepositoryUrl (Join-Path $testRoot 'different-origin') | Out-Null
  } 'origin mismatch'

  $corruptRoot = Join-Path $testRoot 'c'
  $corruptPath = Join-Path $corruptRoot (Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $pinA)
  New-Item -ItemType Directory -Path $corruptPath -Force | Out-Null
  Assert-Throws 'cache directory without a Git checkout' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $corruptRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo | Out-Null
  } 'without Git metadata'

  $interruptedRoot = Join-Path $testRoot 'i'
  $interruptedPath = Join-Path $interruptedRoot (Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $pinA)
  New-Item -ItemType Directory -Path (Join-Path $interruptedPath '.git') -Force | Out-Null
  Assert-Throws 'interrupted partial clone' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $interruptedRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo | Out-Null
  } 'invalid or incomplete'
  Assert-True (Test-Path -LiteralPath $interruptedPath) 'Fail-closed validation unexpectedly deleted the interrupted cache.'

  $dirtyRoot = Join-Path $testRoot 'd'
  New-Item -ItemType Directory -Path $dirtyRoot -Force | Out-Null
  $dirtyPath = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $dirtyRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
  [System.IO.File]::AppendAllText((Join-Path $dirtyPath 'README.md'), 'tampered')
  Assert-Throws 'modified cache checkout' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $dirtyRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo | Out-Null
  } 'modified or incomplete'

  $cloneFailureRoot = Join-Path $testRoot 'x'
  New-Item -ItemType Directory -Path $cloneFailureRoot -Force | Out-Null
  Assert-Throws 'clone failure' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $cloneFailureRoot -SkillWorkflowSha $pinA -RepositoryUrl (Join-Path $testRoot 'missing-origin') | Out-Null
  } 'clone failed'

  $legacyRoot = Join-Path $testRoot 'l'
  $legacyEvidence = Join-Path $legacyRoot 'evidence\onnx01'
  New-Item -ItemType Directory -Path $legacyEvidence -Force | Out-Null
  $evidenceSentinel = Join-Path $legacyEvidence 'acceptance.json'
  [System.IO.File]::WriteAllText($evidenceSentinel, '{"preserve":"acceptance evidence"}')
  $legacyPath = Join-Path $legacyEvidence ("Skill_Workflow-" + $pinA)
  Invoke-TestGitAtPath $legacyEvidence @('clone', '--quiet', $fixtureRepo, $legacyPath) | Out-Null
  Invoke-TestGit $legacyPath @('checkout', '--quiet', $pinA) | Out-Null
  $migrated = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $legacyRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
  Assert-True ($migrated -eq [System.IO.Path]::GetFullPath((Join-Path $legacyRoot (Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $pinA)))) 'Verified legacy clone did not move to the safe cache.'
  Assert-True (-not (Test-Path -LiteralPath $legacyPath)) 'Legacy Markdown clone still occupies the scanned evidence path.'
  Assert-True (Test-Path -LiteralPath $evidenceSentinel) 'Legacy handling removed unrelated acceptance evidence.'
  Assert-True ((Invoke-TestGit $migrated @('rev-parse', 'HEAD')) -eq $pinA) 'Migrated cache did not preserve the exact pinned checkout.'
  Write-Host '[PASS] verified legacy clone moved intact; acceptance evidence remained.'

  $duplicateRoot = Join-Path $testRoot 'q'
  New-Item -ItemType Directory -Path $duplicateRoot -Force | Out-Null
  $duplicateCache = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $duplicateRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
  $duplicateEvidence = Join-Path $duplicateRoot 'evidence\onnx01'
  New-Item -ItemType Directory -Path $duplicateEvidence -Force | Out-Null
  [System.IO.File]::WriteAllText((Join-Path $duplicateEvidence 'acceptance.json'), '{"preserve":true}')
  $duplicateLegacy = Join-Path $duplicateEvidence ("Skill_Workflow-" + $pinA)
  Invoke-TestGitAtPath $duplicateEvidence @('clone', '--quiet', $fixtureRepo, $duplicateLegacy) | Out-Null
  Invoke-TestGit $duplicateLegacy @('checkout', '--quiet', $pinA) | Out-Null
  $duplicateResult = Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $duplicateRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo
  $legacyQuarantine = Join-Path $duplicateRoot 'frontend\node_modules\.cache\max-skill-workflow\legacy'
  $quarantinedClones = @(Get-ChildItem -LiteralPath $legacyQuarantine -Directory -Force)
  Assert-True ($duplicateResult -eq $duplicateCache) 'Duplicate valid cache changed the selected default checkout.'
  Assert-True (-not (Test-Path -LiteralPath $duplicateLegacy)) 'Duplicate legacy Markdown clone remains under evidence.'
  Assert-True ($quarantinedClones.Count -eq 1) 'Duplicate legacy clone was not preserved in quarantine.'
  Assert-True ((Invoke-TestGit $quarantinedClones[0].FullName @('rev-parse', 'HEAD')) -eq $pinA) 'Quarantined duplicate did not preserve its exact identity.'
  Assert-True (Test-Path -LiteralPath (Join-Path $duplicateEvidence 'acceptance.json')) 'Duplicate-cache migration removed unrelated acceptance evidence.'
  Write-Host '[PASS] verified duplicate legacy clone quarantined intact without replacing the selected cache.'

  $unsafeLegacyRoot = Join-Path $testRoot 'u'
  $unsafeLegacy = Join-Path $unsafeLegacyRoot ("evidence\onnx01\Skill_Workflow-" + $pinA)
  New-Item -ItemType Directory -Path $unsafeLegacy -Force | Out-Null
  [System.IO.File]::WriteAllText((Join-Path $unsafeLegacy 'README.md'), 'Not a verified Git checkout.')
  Assert-Throws 'unverified legacy cache contamination' {
    Resolve-Onnx01SkillWorkflowRoot -RepositoryRoot $unsafeLegacyRoot -SkillWorkflowSha $pinA -RepositoryUrl $fixtureRepo | Out-Null
  } 'cannot be safely relocated'
  Assert-True (Test-Path -LiteralPath $unsafeLegacy) 'Unverified legacy directory was moved or deleted.'

  $ignoredEvidence = Invoke-TestGit $repoRoot @('check-ignore', '-q', 'evidence/onnx01/acceptance.json')
  Assert-True ($LASTEXITCODE -eq 0) 'Acceptance evidence path is not ignored by Git.'
  $ignoredCacheOutput = @(& git -C $repoRoot check-ignore -q (Join-Path 'frontend/node_modules/.cache/max-skill-workflow' $pinA) 2>&1)
  Assert-True ($LASTEXITCODE -eq 0) 'Default Skill Workflow cache is not ignored by Git.'
  Write-Host '[PASS] acceptance evidence and default cache paths are Git-ignored.'

  Assert-True ((Invoke-TestGit $repoRoot @('rev-parse', 'HEAD')) -eq $originalHead) 'Candidate HEAD changed during cache tests.'
  Assert-True ((Invoke-TestGit $repoRoot @('rev-parse', 'HEAD^{tree}')) -eq $originalTree) 'Candidate tree changed during cache tests.'
  Assert-True ((Invoke-TestGit $repoRoot @('status', '--porcelain', '--untracked-files=all')) -eq $originalStatus) 'Cache tests changed the candidate worktree.'
  Write-Host '[PASS] candidate HEAD/tree and pre-existing worktree status remained unchanged.'
  Write-Host 'ONNX01_SKILL_CACHE_REGRESSION=PASS'
} finally {
  $resolvedTestStorageRoot = [System.IO.Path]::GetFullPath($testStorageRoot).TrimEnd('\') + '\'
  $resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot).TrimEnd('\') + '\'
  if (-not $resolvedTestRoot.StartsWith($resolvedTestStorageRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing test cleanup outside the exact Git-metadata test parent: $testRoot"
  }
  if ((Test-Path -LiteralPath $testMarker) -and (Get-Content -LiteralPath $testMarker -Raw) -like '*owned by test_onnx01_skill_workflow_cache.ps1*') {
    Remove-OwnedOnnx01TestDirectory $testRoot
  } elseif (Test-Path -LiteralPath $testRoot) {
    throw "Refusing to remove test directory without its ownership marker: $testRoot"
  }
}
