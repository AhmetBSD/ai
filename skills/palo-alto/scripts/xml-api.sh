#!/usr/bin/env bash
# PAN-OS XML API helper — debug için, production değişiklik playbook'tan
# Usage: xml-api.sh <type> <action> [extra params]
#   xml-api.sh op cmd '<show><system><info/></system></show>'
#   xml-api.sh config get '/config/devices/entry/vsys/entry[@name="vsys1"]/address'
set -euo pipefail

: "${PANOS_HOST:?Set PANOS_HOST=fw01.example.com}"
: "${PANOS_API_KEY:?Set PANOS_API_KEY (get via panos_api_key module)}"

TYPE="${1:?type: op|config|commit|report|export}"
ACTION="${2:-}"
EXTRA="${3:-}"

CA_BUNDLE="${PANOS_CA_BUNDLE:-}"
if [[ -z "$CA_BUNDLE" ]]; then
  echo "ERROR: PANOS_CA_BUNDLE not set — TLS verify YASAK bypass (§1)" >&2
  echo "       Set PANOS_CA_BUNDLE=/path/to/internal-ca.pem" >&2
  exit 1
fi

case "$TYPE" in
  op)
    curl --cacert "$CA_BUNDLE" -sG "https://$PANOS_HOST/api/" \
      --data-urlencode "type=op" \
      --data-urlencode "cmd=$ACTION" \
      --data-urlencode "key=$PANOS_API_KEY"
    ;;
  config)
    curl --cacert "$CA_BUNDLE" -sG "https://$PANOS_HOST/api/" \
      --data-urlencode "type=config" \
      --data-urlencode "action=${ACTION:-get}" \
      --data-urlencode "xpath=$EXTRA" \
      --data-urlencode "key=$PANOS_API_KEY"
    ;;
  commit)
    curl --cacert "$CA_BUNDLE" -sG "https://$PANOS_HOST/api/" \
      --data-urlencode "type=commit" \
      --data-urlencode "cmd=<commit><description>${ACTION:-}</description></commit>" \
      --data-urlencode "key=$PANOS_API_KEY"
    ;;
  *)
    echo "Unknown type: $TYPE" >&2
    exit 1
    ;;
esac
echo
