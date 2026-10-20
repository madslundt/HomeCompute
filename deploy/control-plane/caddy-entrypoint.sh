#!/bin/sh
set -eu

# Provision with scripts/setup-network-inference.py before starting Caddy.
# Keep this credential in durable state with the LiteLLM key database.
LITELLM_NETWORK_KEY="$(cat /data/network-inference-key)"
[ -n "$LITELLM_NETWORK_KEY" ] || {
    printf 'Network inference key is missing or empty\n' >&2
    exit 1
}
export LITELLM_NETWORK_KEY
exec "$@"
