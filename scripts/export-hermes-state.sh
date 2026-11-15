#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

# Emit a private tar stream containing the active Hermes owner's durable
# native-home state. Run as hermes-operator in the agents guest; the caller is
# responsible for encrypted off-host storage.

readonly CLI=/home/hermes-operator/.local/bin/nemohermes
readonly OWNER=hermes-personal-v2
readonly MAX_BYTES=$((20 * 1024 * 1024 * 1024))
readonly SNAPSHOT_SCRIPT="$HOME/.local/share/homecompute-hermes-backup/hermes-native-home-snapshot.py"

[[ $(id -un) == hermes-operator ]] || { echo 'Hermes export must run as hermes-operator.' >&2; exit 1; }
[[ -z ${SSH_ORIGINAL_COMMAND:-} ]] || { echo 'Remote commands are not accepted.' >&2; exit 1; }
[[ -x $CLI ]] || { echo 'Pinned NemoClaw CLI is unavailable.' >&2; exit 1; }
[[ -f $SNAPSHOT_SCRIPT && ! -L $SNAPSHOT_SCRIPT ]] || { echo 'Tracked Hermes snapshot helper is not installed.' >&2; exit 1; }
command -v jq >/dev/null || { echo 'jq is required.' >&2; exit 1; }

export NEMOCLAW_GATEWAY_PORT=8080
export OPENSHELL_GATEWAY=nemoclaw

list_json="$($CLI list --json 2>/dev/null)"
jq -e --arg name "$OWNER" 'any(.sandboxes[]; .name == $name)' <<<"$list_json" >/dev/null || {
  echo "Expected Hermes sandbox is not registered: $OWNER" >&2
  exit 1
}
if jq -e --arg name "$OWNER" 'any(.sandboxes[]; .name == $name and (.activeSessionCount // 0) > 0)' \
  <<<"$list_json" >/dev/null; then
  echo 'Hermes has an active session; refusing a state export.' >&2
  exit 75
fi

owner_state="$($CLI "$OWNER" status --json 2>/dev/null)"
jq -e '.phase == "Ready"' <<<"$owner_state" >/dev/null || {
  echo 'The owner Hermes sandbox is not ready.' >&2
  exit 1
}

temporary="$(mktemp -d "$HOME/.local/state/homecompute-hermes-export.XXXXXX")"
cleanup() {
  local result=$?
  "$CLI" "$OWNER" exec --timeout 30 -- rm -f /tmp/homecompute-hermes-native-home.tar.gz >/dev/null 2>&1 || true
  rm -rf -- "$temporary"
  exit "$result"
}
trap cleanup EXIT HUP INT TERM

"$CLI" "$OWNER" exec --timeout 900 --stdin -- python3 - <"$SNAPSHOT_SCRIPT" >/dev/null
list_json="$($CLI list --json 2>/dev/null)"
if jq -e --arg name "$OWNER" 'any(.sandboxes[]; .name == $name and (.activeSessionCount // 0) > 0)' \
  <<<"$list_json" >/dev/null; then
  echo 'A Hermes session started during the export; refusing to emit this snapshot.' >&2
  exit 75
fi
"$CLI" "$OWNER" download /tmp/homecompute-hermes-native-home.tar.gz \
  "$temporary/hermes-native-home.tar.gz" >/dev/null
[[ -f $temporary/hermes-native-home.tar.gz && ! -L $temporary/hermes-native-home.tar.gz ]] || {
  echo 'Hermes native-home archive is absent or unsafe.' >&2
  exit 1
}
size="$(stat -c %s -- "$temporary/hermes-native-home.tar.gz")"
[[ $size =~ ^[0-9]+$ && $size -le $MAX_BYTES ]] || {
  echo 'Hermes native-home export exceeds the 20 GiB safety limit.' >&2
  exit 1
}
cat -- "$temporary/hermes-native-home.tar.gz"
