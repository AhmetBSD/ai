# Windows / PowerShell installer for the palo-alto skill.
# Idempotent — safe to re-run.
#
# One-line install for the customer:
#   irm https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.ps1 | iex

$ErrorActionPreference = 'Stop'

$RepoUrl       = 'https://github.com/AhmetBSD/ai.git'
$RepoBranch    = 'main'
$SkillPath     = 'skills/palo-alto'
$SkillName     = 'palo-alto'

$LocalRepo     = Join-Path $env:LOCALAPPDATA 'ai-skills'
$ClaudeSkills  = Join-Path $env:USERPROFILE '.claude\skills'
$SkillLink     = Join-Path $ClaudeSkills $SkillName

Write-Host "[install] $SkillName -- Claude Code skill"
Write-Host "[install] repo: $RepoUrl"

# 1) git available?
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "git not found. Install Git for Windows first: https://git-scm.com/download/win"
}

# 2) Clone or update the repo
$repoParent = Split-Path $LocalRepo -Parent
if (-not (Test-Path $repoParent)) { New-Item -ItemType Directory -Path $repoParent -Force | Out-Null }

if (Test-Path (Join-Path $LocalRepo '.git')) {
    Write-Host "[install] updating existing repo at $LocalRepo"
    git -C $LocalRepo fetch --quiet origin $RepoBranch
    git -C $LocalRepo reset --quiet --hard "origin/$RepoBranch"
} else {
    Write-Host "[install] cloning to $LocalRepo"
    git clone --quiet --depth 1 --branch $RepoBranch $RepoUrl $LocalRepo
}

# 3) Junction the skill into Claude's discovery path (no admin rights required)
if (-not (Test-Path $ClaudeSkills)) {
    New-Item -ItemType Directory -Path $ClaudeSkills -Force | Out-Null
}
$target = Join-Path $LocalRepo $SkillPath
if (-not (Test-Path $target)) {
    throw "skill path not found in repo: $target"
}

if (Test-Path $SkillLink) {
    $item = Get-Item $SkillLink -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        Remove-Item $SkillLink -Force -Recurse
    } else {
        throw "$SkillLink exists and is not a reparse point. Remove it manually first."
    }
}
New-Item -ItemType Junction -Path $SkillLink -Value $target | Out-Null
Write-Host "[install] linked $SkillLink -> $target"

# 4) Run skill setup (creates Python venv, installs pan-os-python)
$setup = Join-Path $target 'scripts\setup.ps1'
if (-not (Test-Path $setup)) {
    throw "setup.ps1 missing: $setup"
}
Write-Host "[install] running setup.ps1"
& powershell -ExecutionPolicy Bypass -File $setup

Write-Host ""
Write-Host "[install] DONE."
Write-Host ""
Write-Host "Siradaki tek adim -- bir kerelik kimlik girisi:"
Write-Host "  & `"$env:USERPROFILE\.palo-alto\venv\Scripts\python.exe`" `"$env:USERPROFILE\.claude\skills\palo-alto\scripts\auth.py`""
Write-Host ""
Write-Host "Komut firewall host'unu, kullanici adini ve sifreyi soracak (sifre ekranda gorunmez)."
Write-Host "Sonrasinda Claude'a dogal cumle yazmak yeterli, or:"
Write-Host "  `"198.51.100.108'in 80 portu 192.168.1.50:90'a yonlendir`""
