@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\start_neon_chat.py"
if errorlevel 1 pause
