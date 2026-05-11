#!/usr/bin/env bash
# Tek komut commit + job complete bekleme
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESCRIPTION="${1:-commit via Claude palo-alto}"

exec "$SKILL_DIR/scripts/run-playbook.sh" \
  "$SKILL_DIR/playbooks/commit.yml" \
  -e "commit_description=$DESCRIPTION"
