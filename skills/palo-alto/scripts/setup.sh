#!/usr/bin/env bash
# One-time setup: create Python venv and install pan-os-python + deps.
# No credentials are entered or stored here — creds flow via env vars at runtime.
#
# This script is idempotent. Re-running upgrades dependencies in place.
set -euo pipefail

VENV="${PANOS_VENV:-$HOME/.palo-alto/venv}"

# pan-os-python (1.12.x) still imports `distutils.version`, removed from
# the stdlib in Python 3.12+. Workaround: setuptools<81 ships a hook that
# re-exposes the module. Prefer Python 3.13; Python 3.14 untested.
PY_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY_BIN="$(command -v "$candidate")"; break
  fi
done
if [[ -z "$PY_BIN" ]]; then
  echo "ERROR: no python3 found in PATH" >&2; exit 1
fi
PY_VER="$("$PY_BIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$PY_VER" in
  3.10|3.11|3.12|3.13) : ;;
  *) echo "WARNING: Python $PY_VER untested. Recommend 3.13." ;;
esac

if [[ ! -f "$VENV/bin/python" ]]; then
  echo "[setup] creating venv at $VENV (python $PY_VER)"
  mkdir -p "$(dirname "$VENV")"
  "$PY_BIN" -m venv "$VENV"
fi

# Use the venv's own python/pip directly. `source activate` is unreliable
# when this script is invoked via `curl | bash` (no controlling tty / pipe
# stdin can swallow output and skip subsequent commands).
VENV_PY="$VENV/bin/python"
VENV_PIP="$VENV/bin/pip"

echo "[setup] installing/upgrading pan-os-python, pyyaml, setuptools (distutils shim)"
"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PIP" install --quiet "setuptools<81" "pan-os-python>=1.12" "pyyaml>=6.0"

echo "[setup] verifying imports"
"$VENV_PY" - <<'PY'
import setuptools  # noqa: F401
import panos
from panos.firewall import Firewall  # noqa: F401
print(f"  pan-os-python {panos.__version__} OK")
PY

echo
echo "[setup] ready."
echo "Skill calls will be made by Claude with credentials injected via env vars."
echo "Customer just types natural-language requests in chat — no manual steps."
