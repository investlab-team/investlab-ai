$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not (Test-Path -LiteralPath '.env')) { throw '먼저 scripts/setup.ps1을 실행하세요.' }
New-Item -ItemType Directory -Force -Path '.local/tmp' | Out-Null
$env:TEMP = Join-Path $projectRoot '.local/tmp'
$env:TMP = $env:TEMP
& '.venv/Scripts/python.exe' 'scripts/start_chat.py' --no-browser --port 8000
exit $LASTEXITCODE
