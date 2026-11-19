#!/bin/sh
set -eu

PAPERCLIP_LITELLM_KEY="$(cat /run/paperclip/inference-key)"
case "$PAPERCLIP_LITELLM_KEY" in
  sk-*) ;;
  *) echo 'Paperclip inference key refused.' >&2; exit 1 ;;
esac
case "${PAPERCLIP_LITELLM_KEY#sk-}" in
  ''|*[!A-Za-z0-9_-]*) echo 'Paperclip inference key refused.' >&2; exit 1 ;;
esac
export PAPERCLIP_LITELLM_KEY
exec caddy run --config /etc/caddy/Caddyfile --adapter caddyfile
