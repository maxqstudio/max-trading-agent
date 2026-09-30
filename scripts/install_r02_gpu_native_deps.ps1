param(
    [string]$ToolRoot = ""
)

$ErrorActionPreference = "Stop"

$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if ($env:OS -ne "Windows_NT") {
    throw "R02_GPU_NATIVE_DEPS_WINDOWS_ONLY"
}

if (-not $ToolRoot) {
    $ToolRoot = Join-Path $repositoryRoot ".venv\codex-native-deps"
}
$ToolRoot = [System.IO.Path]::GetFullPath($ToolRoot)
$vcpkgRoot = Join-Path $ToolRoot "vcpkg"
$installRoot = Join-Path $ToolRoot "installed"
$nativeTriplet = "x64-windows-static-md"
$vcpkgCommit = "fe8971a72787a862d89588aedfef583c50e3b5a4"
$vcpkgRemote = "https://github.com/microsoft/vcpkg.git"

$vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vswhere)) {
    throw "R02_GPU_MSVC_BUILD_TOOLS_REQUIRED"
}
$vsInstall = (& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
if ($LASTEXITCODE -ne 0 -or -not $vsInstall) {
    throw "R02_GPU_MSVC_X64_TOOLCHAIN_REQUIRED"
}
$devCommand = Join-Path $vsInstall "Common7\Tools\VsDevCmd.bat"
if (-not (Test-Path $devCommand)) {
    throw "R02_GPU_MSVC_DEVELOPER_COMMAND_REQUIRED"
}

$environmentOutput = & $env:ComSpec /d /s /c "call `"$devCommand`" -no_logo -arch=x64 >nul && set"
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_MSVC_ENVIRONMENT_INITIALIZATION_FAILED"
}
foreach ($line in $environmentOutput) {
    $separator = $line.IndexOf("=")
    if ($separator -gt 0) {
        $name = $line.Substring(0, $separator)
        $value = $line.Substring($separator + 1)
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw "R02_GPU_MSVC_COMPILER_NOT_ON_PATH"
}
if (-not (Get-Command cmake.exe -ErrorAction SilentlyContinue)) {
    throw "R02_GPU_CMAKE_REQUIRED"
}

New-Item -ItemType Directory -Path $ToolRoot -Force | Out-Null
if (-not (Test-Path (Join-Path $vcpkgRoot ".git"))) {
    if (Test-Path $vcpkgRoot) {
        throw "R02_GPU_VCPKG_PATH_OCCUPIED"
    }
    & git clone --quiet --filter=blob:none $vcpkgRemote $vcpkgRoot
    if ($LASTEXITCODE -ne 0) {
        throw "R02_GPU_VCPKG_CLONE_FAILED"
    }
}
$actualRemote = (& git -C $vcpkgRoot remote get-url origin).Trim()
if ($LASTEXITCODE -ne 0 -or $actualRemote -ne $vcpkgRemote) {
    throw "R02_GPU_VCPKG_REMOTE_MISMATCH"
}
$dirty = & git -C $vcpkgRoot status --porcelain
if ($LASTEXITCODE -ne 0 -or $dirty) {
    throw "R02_GPU_VCPKG_CHECKOUT_DIRTY"
}
$actualCommit = (& git -C $vcpkgRoot rev-parse HEAD 2>$null).Trim()
if ($actualCommit -ne $vcpkgCommit) {
    & git -C $vcpkgRoot fetch --quiet --depth 1 origin $vcpkgCommit
    if ($LASTEXITCODE -ne 0) {
        throw "R02_GPU_VCPKG_PIN_FETCH_FAILED"
    }
    & git -C $vcpkgRoot checkout --quiet --detach $vcpkgCommit
    if ($LASTEXITCODE -ne 0) {
        throw "R02_GPU_VCPKG_PIN_CHECKOUT_FAILED"
    }
}
$env:VCPKG_ROOT = $vcpkgRoot

& (Join-Path $vcpkgRoot "bootstrap-vcpkg.bat") -disableMetrics
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_VCPKG_BOOTSTRAP_FAILED"
}
& (Join-Path $vcpkgRoot "vcpkg.exe") install `
    "--triplet=$nativeTriplet" `
    "--x-manifest-root=$repositoryRoot" `
    "--x-install-root=$installRoot" `
    --disable-metrics
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_VCPKG_DEPENDENCIES_FAILED"
}

$boostLibraryDirectory = Join-Path $installRoot "$nativeTriplet\lib"
$openclIncludeDirectory = Join-Path $installRoot "$nativeTriplet\include"
$openclLibrary = Join-Path $boostLibraryDirectory "OpenCL.lib"
if (-not (Test-Path (Join-Path $openclIncludeDirectory "CL\cl.h"))) {
    throw "R02_GPU_OPENCL_HEADERS_MISSING"
}
if (-not (Test-Path $openclLibrary)) {
    throw "R02_GPU_OPENCL_LIBRARY_MISSING"
}
$openclBinaryDirectory = Join-Path $installRoot "$nativeTriplet\bin"
if (Test-Path (Join-Path $openclBinaryDirectory "OpenCL.dll")) {
    $env:PATH = "$openclBinaryDirectory;$env:PATH"
    if ($env:GITHUB_PATH) {
        Add-Content -Path $env:GITHUB_PATH -Value $openclBinaryDirectory
    }
}
if (-not (Get-ChildItem $boostLibraryDirectory -Filter "boost_filesystem*.lib" -ErrorAction SilentlyContinue)) {
    throw "R02_GPU_BOOST_FILESYSTEM_LIBRARY_MISSING"
}
if (-not (Test-Path (Join-Path $installRoot "$nativeTriplet\include\boost\algorithm\string\split.hpp"))) {
    throw "R02_GPU_BOOST_ALGORITHM_HEADER_MISSING"
}
if (-not (Test-Path (Join-Path $installRoot "$nativeTriplet\include\boost\lexical_cast.hpp"))) {
    throw "R02_GPU_BOOST_LEXICAL_CAST_HEADER_MISSING"
}
if (-not (Test-Path (Join-Path $installRoot "$nativeTriplet\include\boost\property_tree\ptree.hpp"))) {
    throw "R02_GPU_BOOST_PROPERTY_TREE_HEADER_MISSING"
}
if (-not (Test-Path (Join-Path $installRoot "$nativeTriplet\include\boost\uuid\detail\sha1.hpp"))) {
    throw "R02_GPU_BOOST_UUID_HEADER_MISSING"
}

$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "R02_GPU_PYTHON_REQUIRED"
    }
    $python = $pythonCommand.Source
}
$pythonScriptsDirectory = Split-Path $python -Parent
$env:PATH = "$pythonScriptsDirectory;$env:PATH"
$env:BOOST_ROOT = Join-Path $installRoot $nativeTriplet
$env:BOOST_LIBRARYDIR = $boostLibraryDirectory
$env:OpenCL_INCLUDE_DIR = $openclIncludeDirectory
$env:OpenCL_LIBRARY = $openclLibrary

$sourceCache = Join-Path $ToolRoot "lightgbm-source"
New-Item -ItemType Directory -Path $sourceCache -Force | Out-Null
$sourceArchive = Join-Path $sourceCache "lightgbm-4.7.0.tar.gz"
if (-not (Test-Path $sourceArchive)) {
    & $python -m pip download `
        --no-deps `
        --no-build-isolation `
        --no-binary=lightgbm `
        --dest $sourceCache `
        "lightgbm==4.7.0"
    if ($LASTEXITCODE -ne 0) {
        throw "R02_GPU_LIGHTGBM_SOURCE_DOWNLOAD_FAILED"
    }
}
$sourceHash = (Get-FileHash -LiteralPath $sourceArchive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne "f8e20f682c9aabd000bcf4a7ed8aa6f473c1adfecccae34ec24e823d156f4af0") {
    throw "R02_GPU_LIGHTGBM_SOURCE_SHA256_MISMATCH"
}
$sourceBuildRoot = Join-Path $ToolRoot ("lightgbm-build-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $sourceBuildRoot | Out-Null
$tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $tar) {
    throw "R02_GPU_TAR_REQUIRED"
}
& $tar.Source -xzf $sourceArchive -C $sourceBuildRoot
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_LIGHTGBM_SOURCE_EXTRACT_FAILED"
}
$sourceDirectory = Join-Path $sourceBuildRoot "lightgbm-4.7.0"
if (-not (Test-Path (Join-Path $sourceDirectory "pyproject.toml"))) {
    throw "R02_GPU_LIGHTGBM_SOURCE_LAYOUT_INVALID"
}
$compatibilityPatch = Join-Path $PSScriptRoot "lightgbm-4.7.0-boost-compute-sha1.patch"
$patchTargetDirectory = $sourceDirectory.Replace("\", "/")
& git -C $repositoryRoot apply --check --unsafe-paths -p1 "--directory=$patchTargetDirectory" $compatibilityPatch
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_LIGHTGBM_BOOST_COMPUTE_PATCH_CONTEXT_MISMATCH"
}
& git -C $repositoryRoot apply --unsafe-paths -p1 "--directory=$patchTargetDirectory" $compatibilityPatch
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_LIGHTGBM_BOOST_COMPUTE_PATCH_FAILED"
}
$patchedSha1Source = Get-Content -LiteralPath (Join-Path $sourceDirectory "external_libs\compute\include\boost\compute\detail\sha1.hpp") -Raw
if ($patchedSha1Source -notmatch "BOOST_VERSION >= 108600" -or $patchedSha1Source -notmatch "unsigned char digest\[20\]") {
    throw "R02_GPU_LIGHTGBM_BOOST_COMPUTE_PATCH_NOT_APPLIED"
}

$pipArguments = @(
    "-m", "pip", "install",
    "--force-reinstall",
    "--no-deps",
    "--no-build-isolation",
    "--config-settings=cmake.define.USE_GPU=ON",
    "--config-settings=cmake.define.BOOST_ROOT=$($env:BOOST_ROOT)",
    "--config-settings=cmake.define.BOOST_LIBRARYDIR=$($env:BOOST_LIBRARYDIR)",
    "--config-settings=cmake.define.OpenCL_INCLUDE_DIR=$($env:OpenCL_INCLUDE_DIR)",
    "--config-settings=cmake.define.OpenCL_LIBRARY=$($env:OpenCL_LIBRARY)",
    "--config-settings=cmake.define.CMAKE_SHARED_LINKER_FLAGS=cfgmgr32.lib",
    $sourceDirectory
)
& $python @pipArguments
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_LIGHTGBM_OPENCL_BUILD_FAILED"
}

& $python -c "import lightgbm; assert lightgbm.__version__ == '4.7.0'; print('LIGHTGBM_OPENCL_BUILD=PASS VERSION=' + lightgbm.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw "R02_GPU_LIGHTGBM_IMPORT_FAILED"
}

Write-Host "R02_GPU_NATIVE_DEPS=PASS"
