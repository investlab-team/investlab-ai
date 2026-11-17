@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\prepare_shared_client.ps1" %*
if errorlevel 1 (pause & exit /b 1)
echo Open .env.team locally and fill the shared connection settings.
pause
