@echo off
cd /d "%~dp0"
if not exist .env.toss copy .env.toss.example .env.toss >nul
if not exist .env.sec copy .env.sec.example .env.sec >nul
docker compose --env-file .env --env-file .env.local --env-file .env.neon --env-file .env.toss --env-file .env.sec -f docker/shared/compose.yaml up --build -d
if errorlevel 1 (pause & exit /b 1)
start "" "http://127.0.0.1:8000"
echo Shared Neon chat: http://127.0.0.1:8000
echo Stop: stop-neon-docker.cmd
