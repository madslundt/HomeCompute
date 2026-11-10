#!/bin/sh
set -eu

# Trust Home Core's private MCP relay certificate while retaining public roots
# for hosted MCPs such as Context7.
system_ca_bundle="$(python3 -c 'import certifi; print(certifi.where())')"
cat "$system_ca_bundle" /run/homecompute/home-core-root.crt > /tmp/litellm-ca-bundle.pem
export SSL_CERT_FILE=/tmp/litellm-ca-bundle.pem
export REQUESTS_CA_BUNDLE=/tmp/litellm-ca-bundle.pem

read_secret() {
  value="$(cat "/run/secrets/$1")"
  [ -n "$value" ] || {
    printf 'Secret is empty: %s\n' "$1" >&2
    exit 1
  }
  printf '%s' "$value"
}

COMPUTE_API_KEY="$(read_secret compute_api_key)"
LITELLM_MASTER_KEY="$(read_secret litellm_master_key)"
LITELLM_SALT_KEY="$(read_secret litellm_salt_key)"
N8N_MCP_TOKEN="$(read_secret n8n_mcp_token)"
export COMPUTE_API_KEY LITELLM_MASTER_KEY LITELLM_SALT_KEY N8N_MCP_TOKEN
postgres_password="$(read_secret postgres_app_password)"
export DATABASE_URL="postgresql://litellm:${postgres_password}@postgres:5432/litellm"
unset postgres_password

exec litellm --config "${LITELLM_CONFIG:-/etc/litellm/config.yaml}" --host 0.0.0.0 --port 4000
