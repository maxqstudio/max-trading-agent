@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_max.ps1"
set "RC=%ERRORLEVEL%"

if not "%~1"=="--no-pause" (
  if not "%RC%"=="0" pause
)

exit /b %RC%
