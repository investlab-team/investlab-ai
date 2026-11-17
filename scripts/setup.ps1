$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
New-Item -ItemType Directory -Force -Path '.local/tmp' | Out-Null
$env:TEMP = Join-Path $projectRoot '.local/tmp'
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = Join-Path $projectRoot '.local/uv-cache'
$env:UV_LINK_MODE = 'copy'
$uvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCommand) {
    $uvExecutable = $uvCommand.Source
} else {
    $uvExecutable = Join-Path $projectRoot '.local/tooling/bin/uv.exe'
    if (-not (Test-Path -LiteralPath $uvExecutable)) {
        python -m pip install --target '.local/tooling' 'uv==0.8.22'
        if ($LASTEXITCODE -ne 0) { throw 'uv 설치 실패' }
    }
}
& $uvExecutable sync --locked
if ($LASTEXITCODE -ne 0) { throw '의존성 설치 실패' }
& '.venv/Scripts/python.exe' 'scripts/setup_local.py'
if ($LASTEXITCODE -ne 0) { throw '로컬 설정 생성 실패' }
Write-Output '준비 완료: .env에 DART 키, OpenAI 키, 모델 ID를 입력하고 F5 또는 scripts/dev.ps1을 실행하세요.'
