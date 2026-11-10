#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

env_file="${CONTROL_PLANE_ENV_FILE:-/etc/homecompute/control-plane.env}"
# shellcheck disable=SC1090
source "$env_file"
password_file="${MCPHUB_POSTGRES_PASSWORD_FILE:?MCPHUB_POSTGRES_PASSWORD_FILE is required}"
[[ -s "$password_file" ]] || { printf 'MCPHub PostgreSQL password file is missing or empty.\n' >&2; exit 1; }
password="$(<"$password_file")"
[[ "$password" =~ ^[a-f0-9]{48}$ ]] || { printf 'MCPHub PostgreSQL password must be 48 lowercase hexadecimal characters.\n' >&2; exit 1; }

{
  printf '\\set mcphub_password %s\n' "$password"
  cat "${MCPHUB_POSTGRES_SQL_FILE:-/srv/homecompute/current/deploy/control-plane/mcphub-postgres.sql}"
} | docker exec -i homecompute-control-plane-postgres-1 \
  psql -X -v ON_ERROR_STOP=1 -U postgres -d postgres >/dev/null
unset password
printf 'MCPHub PostgreSQL role and database are ready.\n'
