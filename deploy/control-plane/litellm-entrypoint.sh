#!/bin/sh
set -eu

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
export COMPUTE_API_KEY LITELLM_MASTER_KEY LITELLM_SALT_KEY
postgres_password="$(read_secret postgres_app_password)"
export DATABASE_URL="postgresql://litellm:${postgres_password}@postgres:5432/litellm"
unset postgres_password

exec litellm --config "${LITELLM_CONFIG:-/etc/litellm/config.yaml}" --host 0.0.0.0 --port 4000
