# Auto-update — silently pull latest skill version from GitHub.
# Mirrors update.sh behaviour: 24h cache, silent skip on network failure,
# fast-forward only, refuses to touch divergent local history.
#
# Usage:
#   .\update.ps1                  # default: 24h cache, quiet
#   .\update.ps1 -Force           # ignore cache, pull now
#   .\update.ps1 -MaxAge 3600     # custom cache age in seconds
#   .\update.ps1 -Check           # print JSON status, do NOT pull

param(
    [switch]$Force,
    [switch]$Check,
    [int]$MaxAge = 86400
)

$ErrorActionPreference = 'SilentlyContinue'

$RepoBranch = 'main'
$CacheDir   = Join-Path $env:LOCALAPPDATA 'ai-skills'
$Marker     = Join-Path $CacheDir 'palo-alto.last-check'

# Locate the repo root from this script's location (resolve junction/symlink).
$scriptPath = (Get-Item $PSCommandPath).Target
if (-not $scriptPath) { $scriptPath = $PSCommandPath }
$skillDir = Split-Path (Split-Path $scriptPath -Parent) -Parent
$repoDir  = Split-Path (Split-Path $skillDir -Parent) -Parent

if (-not (Test-Path (Join-Path $repoDir '.git'))) {
    # Not a git checkout (tarball install) — silently skip.
    exit 0
}

if (-not (Test-Path $CacheDir)) { New-Item -ItemType Directory -Path $CacheDir -Force | Out-Null }

$now = [int][double]::Parse((Get-Date -UFormat %s))

if (-not $Force -and -not $Check -and (Test-Path $Marker)) {
    try {
        $last = [int](Get-Content $Marker -ErrorAction Stop)
        if (($now - $last) -lt $MaxAge) { exit 0 }
    } catch { }
}

# Fetch quietly; tolerate transient network errors.
git -C $repoDir fetch --quiet origin $RepoBranch 2>$null
if ($LASTEXITCODE -ne 0) { exit 0 }

$localSha  = (git -C $repoDir rev-parse HEAD).Trim()
$remoteSha = (git -C $repoDir rev-parse "origin/$RepoBranch" 2>$null).Trim()

if (-not $remoteSha -or $localSha -eq $remoteSha) {
    $now | Out-File -FilePath $Marker -Encoding ascii -NoNewline
    if ($Check) {
        Write-Output (@{ update_available = $false; local = $localSha } | ConvertTo-Json -Compress)
    }
    exit 0
}

if ($Check) {
    Write-Output (@{ update_available = $true; local = $localSha; remote = $remoteSha } | ConvertTo-Json -Compress)
    exit 0
}

git -C $repoDir merge-base --is-ancestor $localSha $remoteSha 2>$null
if ($LASTEXITCODE -eq 0) {
    git -C $repoDir reset --quiet --hard "origin/$RepoBranch" | Out-Null
    $now | Out-File -FilePath $Marker -Encoding ascii -NoNewline
    Write-Host "[update] palo-alto skill: $localSha -> $remoteSha"
} else {
    Write-Host "[update] palo-alto: local diverged from origin/$RepoBranch, leaving alone."
    $now | Out-File -FilePath $Marker -Encoding ascii -NoNewline
}
