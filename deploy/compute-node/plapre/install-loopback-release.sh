#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

[[ ${EUID} -eq 0 ]] || { printf 'Run with sudo: %s\n' "$0" >&2; exit 1; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RELEASE_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd)"
SOURCE_CONFIG="$RELEASE_ROOT/config/plapre-tts.env"
TARGET_CONFIG=/etc/gb10-ai/plapre-tts.env
SETUP="$RELEASE_ROOT/scripts/setup-compute-plapre.sh"

[[ -f "$SOURCE_CONFIG" && ! -L "$SOURCE_CONFIG" ]] || {
  printf 'Missing release config: %s\n' "$SOURCE_CONFIG" >&2
  exit 1
}
grep -qx 'PLAPRE_BIND_ADDRESS=127.0.0.1' "$SOURCE_CONFIG" || {
  printf 'Release refuses any non-loopback Plapre bind\n' >&2
  exit 1
}
grep -qx 'PLAPRE_VOICE_APPROVAL=approved-household-use' "$SOURCE_CONFIG" || {
  printf 'Release is missing the approved household voice decision\n' >&2
  exit 1
}
grep -qx 'PLAPRE_PRIVATE_INGRESS_CONFIRMED=false' "$SOURCE_CONFIG" || {
  printf 'Loopback release must not claim private ingress approval\n' >&2
  exit 1
}
[[ -f "$SETUP" && ! -L "$SETUP" ]] || {
  printf 'Missing setup program: %s\n' "$SETUP" >&2
  exit 1
}

install -d -m 0750 -o root -g root /etc/gb10-ai
temporary="$(mktemp /etc/gb10-ai/.plapre-tts.env.XXXXXX)"
trap 'rm -f -- "$temporary"' EXIT
install -m 0640 -o root -g root "$SOURCE_CONFIG" "$temporary"
mv -fT "$temporary" "$TARGET_CONFIG"
trap - EXIT

bash "$SETUP" validate --env "$TARGET_CONFIG"
bash "$SETUP" build --env "$TARGET_CONFIG"
bash "$SETUP" up --env "$TARGET_CONFIG"
bash "$SETUP" smoke --env "$TARGET_CONFIG"

printf 'Plapre is healthy on loopback Wyoming tcp://127.0.0.1:10201\n'
