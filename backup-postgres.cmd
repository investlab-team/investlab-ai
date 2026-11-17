@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m investlab_ai.local_database backup
pause
