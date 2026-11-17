@echo off
cd /d "%~dp0"
docker compose --env-file .env --env-file .env.local --env-file .env.neon --env-file .env.toss --env-file .env.sec -f docker/shared/compose.yaml down
if errorlevel 1 pause
