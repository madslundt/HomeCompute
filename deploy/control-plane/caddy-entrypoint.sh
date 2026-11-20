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

# Separate from the local-model key used by the guest and legacy listeners.
LITELLM_LAN_CATALOG_KEY="$(cat /data/lan-catalog-key)"
[ -n "$LITELLM_LAN_CATALOG_KEY" ] || {
    printf 'LAN catalog key is missing or empty\n' >&2
    exit 1
}
export LITELLM_LAN_CATALOG_KEY
exec "$@"
