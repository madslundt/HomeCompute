#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

version=v0.32.0
archive_name=immich-go_Linux_x86_64.tar.gz
archive_sha256=6e2ad86bafdadb9466d6515de7cb882726c0aea1a21d51164dff361d7d480a97

usage() {
  printf 'Usage: IMMICH_URL=http://home-core:2283 IMMICH_API_KEY_FILE=/run/secrets/immich/import-api-key %s TAKEOUT_DIRECTORY\n' "$0" >&2
}

[[ $# == 1 ]] || { usage; exit 2; }
takeout_dir="$(cd -- "$1" 2>/dev/null && pwd -P)" || {
  printf 'Takeout directory does not exist.\n' >&2
  exit 1
}
[[ -n ${IMMICH_URL:-} ]] || { printf 'Set IMMICH_URL explicitly.\n' >&2; exit 1; }
[[ -n ${IMMICH_API_KEY_FILE:-} && -r $IMMICH_API_KEY_FILE ]] || {
  printf 'Set IMMICH_API_KEY_FILE to a readable runtime secret.\n' >&2
  exit 1
}
[[ $IMMICH_URL == http://* || $IMMICH_URL == https://* ]] || {
  printf 'IMMICH_URL must use http:// or https://.\n' >&2
  exit 1
}

shopt -s nullglob
archives=("$takeout_dir"/*.zip)
(( ${#archives[@]} > 0 )) || {
  printf 'No Google Takeout ZIP archives found; source files were left untouched.\n' >&2
  exit 1
}

IFS= read -r IMMICH_GO_UPLOAD_API_KEY <"$IMMICH_API_KEY_FILE"
[[ -n $IMMICH_GO_UPLOAD_API_KEY ]] || { printf 'Immich API key secret is empty.\n' >&2; exit 1; }
export IMMICH_GO_UPLOAD_API_KEY IMMICH_GO_UPLOAD_SERVER="$IMMICH_URL"

temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/homecompute-immich-go.XXXXXX")"
trap 'rm -rf -- "$temporary_dir"' EXIT
archive_path="$temporary_dir/$archive_name"
curl --fail --location --silent --show-error \
  "https://github.com/simulot/immich-go/releases/download/$version/$archive_name" \
  --output "$archive_path"
printf '%s  %s\n' "$archive_sha256" "$archive_path" | sha256sum --check --status || {
  printf 'Downloaded immich-go archive failed its pinned SHA256 check.\n' >&2
  exit 1
}
tar -xzf "$archive_path" -C "$temporary_dir" immich-go
"$temporary_dir/immich-go" upload from-google-photos "${archives[@]}"
printf '\nGoogle Takeout import completed. Source archives remain in: %s\n' "$takeout_dir"
