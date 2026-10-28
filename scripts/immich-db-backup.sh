#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

[[ $EUID == 0 ]] || { printf 'Run as root.\n' >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${IMMICH_ENV_FILE:-/etc/homecompute/immich.env}"
COMPOSE_FILE="${IMMICH_COMPOSE_FILE:-$REPO_ROOT/deploy/immich/compose.yaml}"
PASSWORD_FILE="${IMMICH_DATABASE_PASSWORD_FILE:-/run/secrets/immich/database-password}"

[[ -r $ENV_FILE && -r $PASSWORD_FILE && -f $COMPOSE_FILE ]] || {
  printf 'Immich environment, database secret, or Compose file is unavailable.\n' >&2
  exit 1
}

db_backups_path=""
while IFS='=' read -r key value; do
  if [[ $key == IMMICH_DB_BACKUPS_PATH ]]; then
    db_backups_path=$value
    break
  fi
done <"$ENV_FILE"
[[ $db_backups_path == /* && -d $db_backups_path ]] || {
  printf 'Immich database dump directory is missing or invalid.\n' >&2
  exit 1
}

DB_PASSWORD="$(<"$PASSWORD_FILE")"
[[ $DB_PASSWORD =~ ^[A-Za-z0-9]+$ ]] || {
  printf 'Immich database secret must be a non-empty alphanumeric password.\n' >&2
  exit 1
}
export DB_PASSWORD

compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" config --quiet

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
final_path="$db_backups_path/immich-postgres-$timestamp.sql.gz"
temporary_path="$(mktemp "$db_backups_path/.immich-postgres.XXXXXX")"
trap 'rm -f -- "$temporary_path"' EXIT
chmod 0600 "$temporary_path"

# The variable is intentionally expanded by the shell inside the database container.
# shellcheck disable=SC2016
"${compose[@]}" exec --no-TTY database sh -ceu \
  'exec pg_dumpall --clean --if-exists --username="$POSTGRES_USER"' |
  gzip -n >"$temporary_path"

python3 - "$temporary_path" "$final_path" "$db_backups_path" <<'PY'
import os
import sys

temporary, final, directory = sys.argv[1:]
with open(temporary, "rb") as dump:
    os.fsync(dump.fileno())
os.replace(temporary, final)
directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
trap - EXIT

mapfile -t dumps < <(find "$db_backups_path" -maxdepth 1 -type f \
  -name 'immich-postgres-*.sql.gz' -printf '%f\n' | LC_ALL=C sort -r)
for old_dump in "${dumps[@]:14}"; do
  rm -f -- "$db_backups_path/$old_dump"
done

printf 'Created application-consistent Immich database dump: %s\n' "$final_path"
