function Invoke-Onnx01Git {
  param(
    [Parameter(Mandatory = $true)][string[]]$GitArguments,
    [string]$WorkingDirectory
  )

  $previousPreference = $ErrorActionPreference
  try {
    $ErrorActionPreference = 'Continue'
    if ($WorkingDirectory) {
      $output = @(& git -C $WorkingDirectory @GitArguments 2>&1)
    } else {
      $output = @(& git @GitArguments 2>&1)
    }
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousPreference
  }

  $text = (($output | ForEach-Object { [string]$_ }) -join "`n").Trim()
  if ($exitCode -ne 0) {
    throw "git $($GitArguments -join ' ') failed with exit $exitCode. $text"
  }
  return $text
}

function ConvertTo-Onnx01CanonicalPath {
  param([Parameter(Mandatory = $true)][string]$Path)
  return [System.IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-Onnx01NoReparseComponents {
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$RelativePath
  )

  $root = ConvertTo-Onnx01CanonicalPath $RepositoryRoot
  $rootItem = Get-Item -LiteralPath $root -Force
  if (-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint)) {
    throw "Repository root is not a regular directory: $root"
  }

  $current = $root
  foreach ($part in ($RelativePath -split '[\\/]')) {
    if (-not $part) { continue }
    $current = Join-Path $current $part
    if (Test-Path -LiteralPath $current) {
      $item = Get-Item -LiteralPath $current -Force
      if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Refusing cache/evidence path containing a reparse point: $current"
      }
    }
  }
}

function Get-Onnx01CheckoutIdentity {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$ExpectedSha,
    [string]$ExpectedOrigin
  )

  if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "Skill Workflow checkout directory is missing: $Path"
  }
  $resolvedPath = ConvertTo-Onnx01CanonicalPath $Path
  $checkoutItem = Get-Item -LiteralPath $resolvedPath -Force
  if ($checkoutItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw "Skill Workflow checkout may not be a symlink or junction: $resolvedPath"
  }

  $gitMetadataPath = Join-Path $resolvedPath '.git'
  if (-not (Test-Path -LiteralPath $gitMetadataPath)) {
    throw "Skill Workflow cache exists without Git metadata: $resolvedPath"
  }
  $gitMetadata = Get-Item -LiteralPath $gitMetadataPath -Force
  if ($gitMetadata.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
    throw "Skill Workflow Git metadata may not be a symlink or junction: $gitMetadataPath"
  }

  try {
    $gitRoot = Invoke-Onnx01Git -WorkingDirectory $resolvedPath -GitArguments @('rev-parse', '--show-toplevel')
  } catch {
    throw "Skill Workflow cache Git checkout is invalid or incomplete: $resolvedPath. No files were removed. Cause: $($_.Exception.Message)"
  }
  if ((ConvertTo-Onnx01CanonicalPath $gitRoot) -ne $resolvedPath) {
    throw "Skill Workflow path is not the Git checkout root: $resolvedPath (Git root: $gitRoot)"
  }
  $head = Invoke-Onnx01Git -WorkingDirectory $resolvedPath -GitArguments @('rev-parse', 'HEAD')
  if ($head -ne $ExpectedSha) {
    throw "Skill Workflow pin mismatch: expected $ExpectedSha, observed $head at $resolvedPath"
  }

  if ($ExpectedOrigin) {
    $origin = Invoke-Onnx01Git -WorkingDirectory $resolvedPath -GitArguments @('remote', 'get-url', 'origin')
    if (-not [string]::Equals($origin, $ExpectedOrigin, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Skill Workflow origin mismatch at $resolvedPath; expected '$ExpectedOrigin', observed '$origin'."
    }
  }

  $dirty = Invoke-Onnx01Git -WorkingDirectory $resolvedPath -GitArguments @('status', '--porcelain', '--untracked-files=all')
  if ($dirty) {
    throw "Skill Workflow checkout is modified or incomplete: $resolvedPath. No cache files were deleted."
  }

  return [pscustomobject]@{
    Path = $resolvedPath
    Head = $head
    Origin = if ($ExpectedOrigin) { $origin } else { $null }
  }
}

function Move-Onnx01LegacySkillWorkflowCache {
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$SkillWorkflowSha,
    [Parameter(Mandatory = $true)][string]$RepositoryUrl,
    [Parameter(Mandatory = $true)][string]$LegacyPath,
    [Parameter(Mandatory = $true)][string]$CachePath,
    [Parameter(Mandatory = $true)][string]$CacheRoot,
    [bool]$CacheAlreadyExists
  )

  if (-not (Test-Path -LiteralPath $LegacyPath)) { return }
  try {
    $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $RepositoryRoot -RelativePath (Join-Path 'evidence\onnx01' (Split-Path $LegacyPath -Leaf))
    $null = Get-Onnx01CheckoutIdentity -Path $LegacyPath -ExpectedSha $SkillWorkflowSha -ExpectedOrigin $RepositoryUrl
  } catch {
    throw "Legacy Skill Workflow cache may contaminate documentation scans and cannot be safely relocated. No files were moved or deleted. Verify its identity, then move only '$LegacyPath' outside the repository; preserve acceptance evidence. Cause: $($_.Exception.Message)"
  }

  if (-not $CacheAlreadyExists) {
    $destination = $CachePath
    $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $RepositoryRoot -RelativePath 'frontend\node_modules\.cache\max-skill-workflow'
    New-Item -ItemType Directory -Force -Path $CacheRoot | Out-Null
  } else {
    $quarantineRoot = Join-Path $CacheRoot 'legacy'
    $relativeQuarantine = 'frontend\node_modules\.cache\max-skill-workflow\legacy'
    $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $RepositoryRoot -RelativePath $relativeQuarantine
    New-Item -ItemType Directory -Force -Path $quarantineRoot | Out-Null
    $destination = Join-Path $quarantineRoot ($SkillWorkflowSha + '-' + [Guid]::NewGuid().ToString('N'))
  }

  if (Test-Path -LiteralPath $destination) {
    throw "Refusing to overwrite existing Skill Workflow cache destination: $destination"
  }
  Move-Item -LiteralPath $LegacyPath -Destination $destination
  Write-Verbose "Moved the verified legacy Skill Workflow clone to the ignored per-worktree cache: $destination"
}

function Resolve-Onnx01SkillWorkflowRoot {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$RepositoryRoot,
    [Parameter(Mandatory = $true)][string]$SkillWorkflowSha,
    [Parameter(Mandatory = $true)][string]$RepositoryUrl,
    [string]$SkillWorkflowPath = ''
  )

  if ($SkillWorkflowSha -notmatch '^[0-9a-f]{40}$') {
    throw "Invalid pinned Skill Workflow SHA: $SkillWorkflowSha"
  }
  $root = ConvertTo-Onnx01CanonicalPath (Resolve-Path -LiteralPath $RepositoryRoot).Path
  $cacheRelative = Join-Path 'frontend\node_modules\.cache\max-skill-workflow' $SkillWorkflowSha
  $cacheRootRelative = 'frontend\node_modules\.cache\max-skill-workflow'
  $cacheRoot = Join-Path $root $cacheRootRelative
  $cachePath = Join-Path $root $cacheRelative
  $legacyRelative = Join-Path 'evidence\onnx01' ('Skill_Workflow-' + $SkillWorkflowSha)
  $legacyPath = Join-Path $root $legacyRelative

  $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $root -RelativePath $cacheRelative
  $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $root -RelativePath $legacyRelative

  $cacheExists = Test-Path -LiteralPath $cachePath
  if ($cacheExists) {
    $null = Get-Onnx01CheckoutIdentity -Path $cachePath -ExpectedSha $SkillWorkflowSha -ExpectedOrigin $RepositoryUrl
  }

  Move-Onnx01LegacySkillWorkflowCache -RepositoryRoot $root `
    -SkillWorkflowSha $SkillWorkflowSha `
    -RepositoryUrl $RepositoryUrl `
    -LegacyPath $legacyPath `
    -CachePath $cachePath `
    -CacheRoot $cacheRoot `
    -CacheAlreadyExists $cacheExists

  if ($SkillWorkflowPath) {
    $overrideCandidate = if ([System.IO.Path]::IsPathRooted($SkillWorkflowPath)) {
      $SkillWorkflowPath
    } else {
      Join-Path $root $SkillWorkflowPath
    }
    try {
      $overridePath = (Resolve-Path -LiteralPath $overrideCandidate).Path
      $null = Get-Onnx01CheckoutIdentity -Path $overridePath -ExpectedSha $SkillWorkflowSha
      return (ConvertTo-Onnx01CanonicalPath $overridePath)
    } catch {
      throw "Explicit SkillWorkflowPath is not a clean checkout at the required pin ${SkillWorkflowSha}: $overrideCandidate. Cause: $($_.Exception.Message)"
    }
  }

  if (-not (Test-Path -LiteralPath $cachePath)) {
    New-Item -ItemType Directory -Force -Path $cacheRoot | Out-Null
    $null = Assert-Onnx01NoReparseComponents -RepositoryRoot $root -RelativePath $cacheRelative
    try {
      $null = Invoke-Onnx01Git -GitArguments @('clone', '--quiet', $RepositoryUrl, $cachePath)
    } catch {
      throw "Pinned Skill Workflow clone failed at '$cachePath'. The path was left intact for inspection; no cleanup was attempted. Verify or move only this incomplete cache after confirming its identity, then rerun. Cause: $($_.Exception.Message)"
    }

    try {
      $null = Invoke-Onnx01Git -WorkingDirectory $cachePath -GitArguments @('checkout', '--quiet', $SkillWorkflowSha)
    } catch {
      throw "Pinned Skill Workflow checkout failed at '$cachePath'. The path was left intact and will fail closed on reuse; inspect it before any manual relocation. Cause: $($_.Exception.Message)"
    }
  }

  try {
    $null = Get-Onnx01CheckoutIdentity -Path $cachePath -ExpectedSha $SkillWorkflowSha -ExpectedOrigin $RepositoryUrl
  } catch {
    throw "Default Skill Workflow cache failed identity validation at '$cachePath'; it will not be used. Inspect and manually relocate only this cache if appropriate; unrelated files were not changed. Cause: $($_.Exception.Message)"
  }
  return (ConvertTo-Onnx01CanonicalPath $cachePath)
}

Export-ModuleMember -Function Resolve-Onnx01SkillWorkflowRoot
