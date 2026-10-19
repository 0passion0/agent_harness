@echo off
setlocal
set ELECTRON_RUN_AS_NODE=
cd /d "%~dp0desktop"
if not exist "..\.venv\Scripts\python.exe" (
  echo Missing project .venv. Install the Python workspace dependencies first.
  exit /b 1
)
if not exist "node_modules" (
  call npm.cmd ci --no-audit --no-fund
  if errorlevel 1 exit /b 1
)
call npm.cmd run dev
