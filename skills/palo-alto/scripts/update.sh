#!/usr/bin/env bash
# Auto-update — silently pull the latest skill version from GitHub.
#
# Called by Claude before every skill operation. Rate-limited via a marker
# file (default: max once per 24h). Network failure → silent skip, work
# continues with the cached version.
#
# Usage:
#   update.sh                  # default: 86400s cache, quiet
#   update.sh --force          # ignore cache, pull now
#   update.sh --max-age <sec>  # custom cache age
#   update.sh --check          # just print whether an update is available, do NOT pull
set -euo pipefail

REPO_BRANCH="main"
CACHE_DIR="$HOME/.cache/ai-skills"
MARKER="$CACHE_DIR/palo-alto.last-check"
MAX_AGE=86400
FORCE=0
CHECK_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE=1; shift ;;
    --max-age) MAX_AGE="$2"; shift 2 ;;
    --check) CHECK_ONLY=1; shift ;;
    --quiet) shift ;;  # accepted for backward compat, default is quiet
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Locate the repo dir from this script's location (symlink-safe).
SCRIPT_PATH="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "${BASH_SOURCE[0]}")"
SKILL_DIR="$(dirname "$(dirname "$SCRIPT_PATH")")"
# Repo root = two dirs up from skills/palo-alto/scripts/
REPO_DIR="$(cd "$SKILL_DIR/../.." && pwd)"

if [[ ! -d "$REPO_DIR/.git" ]]; then
  # Not a git checkout (e.g. tarball install) — silently skip.
  exit 0
fi

mkdir -p "$CACHE_DIR"

now=$(date +%s)
if [[ $FORCE -eq 0 && $CHECK_ONLY -eq 0 && -f "$MARKER" ]]; then
  last=$(cat "$MARKER" 2>/dev/null || echo 0)
  age=$(( now - last ))
  if (( age < MAX_AGE )); then
    exit 0  # cached, skip
  fi
fi

# Fetch quietly; tolerate transient network errors.
if ! git -C "$REPO_DIR" fetch --quiet origin "$REPO_BRANCH" 2>/dev/null; then
  # Network down? Keep working with current checkout.
  exit 0
fi

local_sha=$(git -C "$REPO_DIR" rev-parse HEAD)
remote_sha=$(git -C "$REPO_DIR" rev-parse "origin/$REPO_BRANCH" 2>/dev/null || echo "")

if [[ -z "$remote_sha" || "$local_sha" == "$remote_sha" ]]; then
  echo "$now" > "$MARKER"
  if [[ $CHECK_ONLY -eq 1 ]]; then
    echo '{"update_available": false, "local": "'"$local_sha"'"}'
  fi
  exit 0
fi

if [[ $CHECK_ONLY -eq 1 ]]; then
  echo '{"update_available": true, "local": "'"$local_sha"'", "remote": "'"$remote_sha"'"}'
  exit 0
fi

# Fast-forward only (refuse to overwrite local changes — shouldn't exist on customer side).
if git -C "$REPO_DIR" merge-base --is-ancestor "$local_sha" "$remote_sha" 2>/dev/null; then
  git -C "$REPO_DIR" reset --quiet --hard "origin/$REPO_BRANCH"
  echo "$now" > "$MARKER"
  echo "[update] palo-alto skill: $local_sha → $remote_sha" >&2
else
  # Diverged history (e.g. customer edited locally). Don't touch it.
  echo "[update] palo-alto: local diverged from origin/$REPO_BRANCH, leaving alone." >&2
  echo "$now" > "$MARKER"
fi
