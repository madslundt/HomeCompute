#!/bin/sh
set -eu

read_secret() {
  value="$(cat "/run/secrets/$1")"
  [ -n "$value" ] || {
    printf 'MCPHub secret is empty: %s\n' "$1" >&2
    exit 1
  }
  printf '%s' "$value"
}

db_password="$(read_secret mcphub_postgres_password)"
admin_password="$(read_secret mcphub_admin_password)"
credential_key="$(read_secret mcphub_credential_encryption_key)"
case "$db_password" in
  *[!a-f0-9]*|'') printf 'MCPHub database password must be lowercase hexadecimal.\n' >&2; exit 1 ;;
esac

export DB_URL="postgresql://mcphub:${db_password}@postgres:5432/mcphub"
export ADMIN_PASSWORD="$admin_password"
export MCPHUB_CREDENTIAL_ENCRYPTION_KEY="$credential_key"
unset db_password admin_password credential_key

exec "$@"
