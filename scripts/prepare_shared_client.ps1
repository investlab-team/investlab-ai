param([string]$ConfigPath = '.env.team')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$target = if ([System.IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $projectRoot $ConfigPath }
$target = [System.IO.Path]::GetFullPath($target)
if (-not (Test-Path -LiteralPath $target)) {
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'docker/shared/.env.example') -Destination $target
}
$original = [System.IO.File]::ReadAllText($target)
$content = $original
foreach ($key in @('INVESTLAB_AI_SERVICE_TOKEN', 'INVESTLAB_AI_INSTANCE_ID')) {
    $pattern = '(?m)^' + [regex]::Escape($key) + '=([^\r\n]*)\r?$'
    $found = [regex]::Match($content, $pattern)
    $present = if ($found.Success) { $found.Groups[1].Value.Trim().Trim('"').Trim("'") } else { '' }
    if ($present) {
        if ($key -eq 'INVESTLAB_AI_SERVICE_TOKEN' -and $present.Length -lt 32) {
            throw 'Existing service token is too short. Set at least 32 random characters; the value was not changed.'
        }
        continue
    }
    if ($key -eq 'INVESTLAB_AI_SERVICE_TOKEN') {
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $value = [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_').TrimEnd('=')
    } else {
        $value = [guid]::NewGuid().ToString('N')
    }
    $line = $key + '=' + $value
    if ($found.Success) {
        $content = $content.Substring(0, $found.Index) + $line + $content.Substring($found.Index + $found.Length)
    } else {
        $content = $content.TrimEnd() + [Environment]::NewLine + $line + [Environment]::NewLine
    }
}
if ($content -ne $original) {
    [System.IO.File]::WriteAllText($target, $content, (New-Object System.Text.UTF8Encoding($false)))
}
Write-Output 'Prepared private team config; existing settings were preserved.'
Write-Output 'Get RESEARCH_DB_URL and storage runtime settings privately from the project owner.'
Write-Output 'Fill your config file, then use the commands in docker/shared/README.md.'
Write-Output 'No credentials were printed. No database connection or migration was performed.'
