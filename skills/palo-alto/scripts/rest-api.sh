#!/usr/bin/env bash
# PAN-OS REST API helper (10.0+)
# Usage: rest-api.sh GET /Objects/Addresses '?location=vsys&vsys=vsys1'
set -euo pipefail

: "${PANOS_HOST:?Set PANOS_HOST=fw01.example.com}"
: "${PANOS_API_KEY:?Set PANOS_API_KEY}"
: "${PANOS_CA_BUNDLE:?Set PANOS_CA_BUNDLE=/path/to/internal-ca.pem (§1 TLS verify mandatory)}"

METHOD="${1:?usage: rest-api.sh <GET|POST|PUT|DELETE> <path> [query] [body-json]}"
PATH_PART="${2:?path required, e.g. /Objects/Addresses}"
QUERY="${3:-}"
BODY="${4:-}"

API_VERSION="${PANOS_API_VERSION:-v10.2}"
URL="https://$PANOS_HOST/restapi/$API_VERSION$PATH_PART$QUERY"

ARGS=(
  --cacert "$PANOS_CA_BUNDLE"
  -s -X "$METHOD"
  -H "X-PAN-KEY: $PANOS_API_KEY"
  -H "Content-Type: application/json"
)

if [[ -n "$BODY" ]]; then
  ARGS+=(--data "$BODY")
fi

curl "${ARGS[@]}" "$URL"
echo
