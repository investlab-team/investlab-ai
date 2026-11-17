@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "scripts\collect_us_top100.py" %*
if errorlevel 1 pause
