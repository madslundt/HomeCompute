#!/bin/sh
set -eu
# Only process-scoped values; secret bytes are never rendered into config/state.
OPENCLAW_GATEWAY_TOKEN="$(cat /run/secrets/openclaw_gateway_token)"
OPENCLAW_MODEL_KEY="$(cat /run/secrets/openclaw_model_key)"
OPENCLAW_BROKER_TOKEN="$(cat /run/secrets/openclaw_broker_token)"
export OPENCLAW_GATEWAY_TOKEN OPENCLAW_MODEL_KEY OPENCLAW_BROKER_TOKEN
for value in "$OPENCLAW_GATEWAY_TOKEN" "$OPENCLAW_MODEL_KEY" "$OPENCLAW_BROKER_TOKEN"; do
  test -n "$value" || { echo 'OpenClaw requires nonempty secret files' >&2; exit 1; }
done
# Externally managed config forbids doctor --fix. Fresh state initializes at
# Gateway startup; retained-state upgrades require the documented offline migration.
exec node /app/dist/index.js gateway --bind lan --port 18789
