#!/usr/bin/env bash
# Wrapper: ansible-vault password prompt + collection path
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${PANOS_VENV:-$HOME/.palo-alto/venv}"

if [[ ! -f "$VENV/bin/activate" ]]; then
  echo "ERROR: venv missing at $VENV — see SKILL.md ön koşullar" >&2
  exit 1
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

PLAYBOOK="${1:?usage: run-playbook.sh <playbook.yml> [extra ansible args]}"
shift

VAULT_FILE="${PANOS_VAULT_FILE:-$HOME/.secrets/panos-vault.yml}"
PROVIDER_FILE="${PANOS_PROVIDER_FILE:-$HOME/.secrets/panos.yml}"

VAULT_ARGS=()
if [[ -f "$VAULT_FILE" ]]; then
  VAULT_ARGS+=(--ask-vault-pass -e "@$VAULT_FILE")
fi

ansible-playbook "$PLAYBOOK" \
  -e "@$PROVIDER_FILE" \
  "${VAULT_ARGS[@]}" \
  "$@"
