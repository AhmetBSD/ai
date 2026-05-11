# One-time setup: Python venv + pan-os-python + setuptools<81 (distutils shim).
# No credentials are entered or stored — creds flow via env vars at runtime.
$ErrorActionPreference = 'Stop'

$Venv = if ($env:PANOS_VENV) { $env:PANOS_VENV } else { Join-Path $env:USERPROFILE '.palo-alto\venv' }

# pan-os-python 1.12.x imports `distutils.version`, removed from stdlib in
# Python 3.12+. setuptools<81 ships _distutils_hack to re-expose it.
# Prefer Python 3.13; Python 3.14 untested with this combo.
$pyBin = $null
foreach ($candidate in @('py -3.13', 'py -3.12', 'py -3.11', 'py -3', 'python3.13', 'python3.12', 'python3.11', 'python')) {
    $exe, $arg = $candidate -split ' ', 2
    if (Get-Command $exe -ErrorAction SilentlyContinue) {
        try {
            $ver = & $exe $arg -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($ver -match '^3\.(1[0-3]|1[12]|1[01])$') {
                $pyBin = $candidate; $pyVer = $ver; break
            } elseif ($ver -match '^3\.') {
                # Note: untested but allow with warning
                if (-not $pyBin) { $pyBin = $candidate; $pyVer = $ver }
            }
        } catch { }
    }
}
if (-not $pyBin) {
    throw "No Python 3.10+ found in PATH. Install from https://www.python.org/downloads/ (3.13 recommended)."
}
Write-Host "[setup] using python $pyVer ($pyBin)"

$venvActivate = Join-Path $Venv 'Scripts\Activate.ps1'
if (-not (Test-Path $venvActivate)) {
    Write-Host "[setup] creating venv at $Venv"
    $venvParent = Split-Path $Venv -Parent
    if (-not (Test-Path $venvParent)) { New-Item -ItemType Directory -Path $venvParent -Force | Out-Null }
    $exe, $arg = $pyBin -split ' ', 2
    & $exe $arg -m venv $Venv
}

$venvPy = Join-Path $Venv 'Scripts\python.exe'
if (-not (Test-Path $venvPy)) {
    throw "venv python.exe missing at $venvPy"
}

Write-Host "[setup] installing/upgrading pan-os-python, pyyaml, setuptools (distutils shim)"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet 'setuptools<81' 'pan-os-python>=1.12' 'pyyaml>=6.0'

Write-Host "[setup] verifying imports"
& $venvPy -c "import setuptools; import panos; from panos.firewall import Firewall; print(f'  pan-os-python {panos.__version__} OK')"

Write-Host ""
Write-Host "[setup] ready."
Write-Host "Skill calls will be made by Claude with credentials injected via env vars."
Write-Host "Customer just types natural-language requests in chat -- no manual steps."
