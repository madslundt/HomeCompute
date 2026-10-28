#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

usage() {
  printf 'Usage: sudo %s SNAPSHOT_ID EMPTY_RESTORE_DIRECTORY\n' "$0" >&2
}
[[ $# == 2 ]] || { usage; exit 2; }
[[ $EUID == 0 ]] || { printf 'Run as root so restored file ownership is preserved.\n' >&2; exit 1; }

snapshot=$1
restore_dir=$2
[[ $snapshot =~ ^[A-Za-z0-9-]+$ ]] || { printf 'Invalid Restic snapshot ID.\n' >&2; exit 2; }
[[ $restore_dir == /* && ! -e $restore_dir ]] || {
  printf 'Restore path must be an absolute path that does not exist yet.\n' >&2
  exit 2
}
case "$restore_dir/" in
  /srv/state/immich/*) printf 'Restore into a temporary location outside production state.\n' >&2; exit 2 ;;
esac
command -v restic-homecompute-immich-hetzner >/dev/null || {
  printf 'The configured Immich-to-Hetzner Restic wrapper is unavailable.\n' >&2
  exit 1
}

install -d -m 0700 "$restore_dir"
restic-homecompute-immich-hetzner restore "$snapshot" \
  --target "$restore_dir" \
  --include /srv/state/immich/library \
  --include /srv/state/immich/db-backups
restored_root="$restore_dir/srv/state/immich"
[[ -d $restored_root/library && -d $restored_root/db-backups ]] || {
  printf 'Snapshot did not restore both the Immich library and database dumps. Files were preserved for inspection.\n' >&2
  exit 1
}
shopt -s nullglob
dumps=("$restored_root"/db-backups/immich-postgres-*.sql.gz)
(( ${#dumps[@]} > 0 )) || {
  printf 'No Immich PostgreSQL dump was restored. Files were preserved for inspection.\n' >&2
  exit 1
}
gzip --test "${dumps[0]}"
printf 'Restored files are under %s/srv/state/immich. Validate them before any production recovery.\n' "$restore_dir"
