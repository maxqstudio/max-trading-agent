@echo off
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m uvicorn max_backend.main:app --app-dir backend --host 127.0.0.1 --port 8000
