#!/usr/bin/env bash
# One-line customer installer. Idempotent — safe to re-run.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/AhmetBSD/ai/main/skills/palo-alto/install.sh | bash
set -euo pipefail

REPO_URL="https://github.com/AhmetBSD/ai.git"
REPO_BRANCH="main"
SKILL_PATH_IN_REPO="skills/palo-alto"
SKILL_NAME="palo-alto"

LOCAL_REPO="$HOME/.local/share/ai-skills"
CLAUDE_SKILLS_DIR="$HOME/.claude/skills"
SKILL_SYMLINK="$CLAUDE_SKILLS_DIR/$SKILL_NAME"

echo "[install] $SKILL_NAME — Claude Code skill"
echo "[install] repo: $REPO_URL"

# 1) git available?
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found. Install git first." >&2
  exit 1
fi

# 2) Clone or update the repo
mkdir -p "$(dirname "$LOCAL_REPO")"
if [[ -d "$LOCAL_REPO/.git" ]]; then
  echo "[install] updating existing repo at $LOCAL_REPO"
  git -C "$LOCAL_REPO" fetch --quiet origin "$REPO_BRANCH"
  git -C "$LOCAL_REPO" reset --quiet --hard "origin/$REPO_BRANCH"
else
  echo "[install] cloning to $LOCAL_REPO"
  git clone --quiet --depth 1 --branch "$REPO_BRANCH" "$REPO_URL" "$LOCAL_REPO"
fi

# 3) Symlink skill into ~/.claude/skills/
mkdir -p "$CLAUDE_SKILLS_DIR"
TARGET="$LOCAL_REPO/$SKILL_PATH_IN_REPO"
if [[ ! -d "$TARGET" ]]; then
  echo "ERROR: skill path not found in repo: $TARGET" >&2
  exit 1
fi
if [[ -L "$SKILL_SYMLINK" ]]; then
  rm "$SKILL_SYMLINK"
elif [[ -e "$SKILL_SYMLINK" ]]; then
  echo "ERROR: $SKILL_SYMLINK exists and is not a symlink. Remove it manually first." >&2
  exit 1
fi
ln -s "$TARGET" "$SKILL_SYMLINK"
echo "[install] linked $SKILL_SYMLINK -> $TARGET"

# 4) Run skill's setup.sh (creates Python venv, installs pan-os-python)
echo "[install] running setup.sh"
bash "$TARGET/scripts/setup.sh"

echo
echo "[install] DONE."
echo "Skill is registered with Claude Code. Open Claude and type a natural-language request, e.g.:"
echo "  \"Firewall 10.0.0.1, admin/MyPass. 198.51.100.108'in 80 portu 192.168.1.50:90'a yönlendir\""
