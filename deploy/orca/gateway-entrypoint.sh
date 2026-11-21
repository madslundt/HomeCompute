#!/bin/sh
set -eu
ORCA_LITELLM_KEY="$(cat /run/orca/inference-key)"
case "${ORCA_LITELLM_KEY#sk-}" in
    ''|*[!A-Za-z0-9_-]*) exit 1 ;;
esac
[ "${ORCA_LITELLM_KEY#sk-}" != "$ORCA_LITELLM_KEY" ]
export ORCA_LITELLM_KEY
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
