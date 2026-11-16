param([switch]$NoBrowser, [ValidateRange(1024,65535)][int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
function Invoke-InvestLabDocker {
    param([string[]]$DockerArgs)
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & docker @DockerArgs
        $investlabExitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousPreference }
    if ($investlabExitCode -ne 0) { throw "Docker command failed: $($DockerArgs[0])" }
}
function New-InvestLabSecret {
    $bytes = New-Object byte[] 36
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return ([Convert]::ToBase64String($bytes)).TrimEnd('=').Replace('+','-').Replace('/','_')
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker Desktop is required.' }
Invoke-InvestLabDocker -DockerArgs @('info', '--format', '{{.ServerVersion}}')
$images = Get-Content -LiteralPath 'images.json' -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath '.env')) {
    $lines = @(
        "INVESTLAB_APP_IMAGE=$($images.app)", "INVESTLAB_DB_IMAGE=$($images.database)",
        "INVESTLAB_AI_OPENAI_MODEL=$($images.model)",
        "INVESTLAB_AI_BASIC_ANALYSIS_TOKENS=$($images.basicAnalysisTokens)",
        "RESEARCH_DB_ADMIN_PASSWORD=$(New-InvestLabSecret)",
        "RESEARCH_DB_PASSWORD=$(New-InvestLabSecret)",
        "INVESTLAB_AI_SERVICE_TOKEN=$(New-InvestLabSecret)",
        "INVESTLAB_CHAT_PORT=$Port", 'INVESTLAB_AI_DART_API_KEY=', 'INVESTLAB_AI_OPENAI_API_KEY='
    )
    [IO.File]::WriteAllLines((Join-Path $PSScriptRoot '.env'), $lines, (New-Object Text.UTF8Encoding($false)))
}
foreach ($image in @($images.app, $images.database)) {
    $ErrorActionPreference = 'Continue'
    & docker image inspect $image *> $null
    $missing = $LASTEXITCODE -ne 0
    $ErrorActionPreference = 'Stop'
    if ($missing) {
        if (Test-Path -LiteralPath 'images.tar.gz') {
            Write-Output 'Loading packaged images. This may take several minutes.'
            $actual = (Get-FileHash -LiteralPath 'images.tar.gz' -Algorithm SHA256).Hash.ToLowerInvariant()
            $expected = (Get-Content -LiteralPath 'images.sha256' -Raw).Split(' ')[0].Trim()
            if ($actual -ne $expected) { throw 'Image archive checksum mismatch. Download the complete bundle again.' }
            Invoke-InvestLabDocker -DockerArgs @('load', '--input', 'images.tar.gz')
            break
        }
        Invoke-InvestLabDocker -DockerArgs @('pull', $image)
    }
}
foreach ($image in @($images.app, $images.database)) {
    $actualId = Invoke-InvestLabDocker -DockerArgs @('image', 'inspect', '--format', '{{.Id}}', $image)
    $expectedId = $images.imageIds.PSObject.Properties[$image].Value
    if ($actualId.Trim() -ne $expectedId) { throw "Image ID differs from images.json: $image" }
}
Write-Output 'Starting InvestLab. The first run restores the included database; please wait.'
Invoke-InvestLabDocker -DockerArgs @('compose', 'up', '--detach', '--wait', '--wait-timeout', '900')
$portLine = Get-Content -LiteralPath '.env' | Where-Object { $_ -match '^INVESTLAB_CHAT_PORT=' } | Select-Object -Last 1
$port = if ($portLine) { ($portLine -split '=',2)[1].Trim() } else { '8000' }
$url = "http://127.0.0.1:$port/"
Write-Output "Chat: $url"
if (-not $NoBrowser) { Start-Process $url }
Write-Output 'Saved analyses work without API keys. To stop, run stop.cmd. Data remains in the volume.'
