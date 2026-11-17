@echo off
cd /d "%~dp0"
if exist ".env.neon" (
  call "%~dp0start-neon-chat.cmd"
  exit /b
)
if not exist ".venv\Scripts\python.exe" (
  echo Run scripts\setup.ps1 first to install the local environment.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\start_chat.py"
if errorlevel 1 pause
