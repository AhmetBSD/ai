#!/usr/bin/env bash
# Running config XML yedek
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${1:?usage: backup-config.sh <output.xml>}"

exec "$SKILL_DIR/scripts/run-playbook.sh" \
  "$SKILL_DIR/playbooks/backup.yml" \
  -e "output_path=$OUTPUT"
