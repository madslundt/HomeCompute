#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

action="${1:-}"
[[ $# == 1 && "$action" =~ ^(prepare|up|down|status)$ ]] || {
  printf 'Usage: sudo bash scripts/setup-home-core-piper.sh prepare|up|down|status\n' >&2
  exit 2
}
[[ $EUID == 0 && $(hostname) == home-core ]] || {
  printf 'Run as root on home-core.\n' >&2
  exit 1
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE=/etc/homecompute/piper-tts.env
MODEL_DIR=/srv/state/piper-tts/models
[[ -r "$ENV_FILE" ]] || { printf 'Missing %s\n' "$ENV_FILE" >&2; exit 1; }

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

required=(PIPER_IMAGE PIPER_LAN_ADDRESS PIPER_WYOMING_PORT PIPER_VOICE_ID PIPER_VOICE_REVISION PIPER_MODEL_SHA256 PIPER_CONFIG_SHA256 PIPER_MODEL_CARD_SHA256 MOSS_SOURCE_REVISION MOSS_TTS_MODEL_REVISION MOSS_CODEC_MODEL_REVISION)
for name in "${required[@]}"; do
  [[ -n "${!name:-}" ]] || { printf 'Missing setting: %s\n' "$name" >&2; exit 1; }
done
[[ "$PIPER_VOICE_ID" == da_DK-talesyntese-medium ]] || { printf 'Unexpected voice id.\n' >&2; exit 1; }

compose=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/deploy/piper-tts/compose.yaml")

verify_file() {
  local path="$1" expected_size="$2" expected_hash="$3"
  [[ -f "$path" && ! -L "$path" ]] || return 1
  [[ $(stat -c %s "$path") == "$expected_size" ]] || return 1
  [[ $(sha256sum "$path" | cut -d ' ' -f 1) == "$expected_hash" ]]
}

fetch_file() {
  local name="$1" expected_size="$2" expected_hash="$3"
  local target="$MODEL_DIR/$name" temporary
  if verify_file "$target" "$expected_size" "$expected_hash"; then
    return
  fi
  [[ ! -e "$target" ]] || { printf 'Refusing to replace invalid artifact: %s\n' "$target" >&2; exit 1; }
  temporary="$(mktemp "$MODEL_DIR/.${name}.part.XXXXXX")"
  trap 'rm -f -- "$temporary"' RETURN
  curl --fail --location --silent --show-error \
    "https://huggingface.co/rhasspy/piper-voices/resolve/$PIPER_VOICE_REVISION/da/da_DK/talesyntese/medium/$name" \
    --output "$temporary"
  verify_file "$temporary" "$expected_size" "$expected_hash" || {
    printf 'Downloaded artifact failed verification: %s\n' "$name" >&2
    exit 1
  }
  chmod 0444 "$temporary"
  mv "$temporary" "$target"
  trap - RETURN
}

verify_all() {
  verify_file "$MODEL_DIR/$PIPER_VOICE_ID.onnx" 63201294 "$PIPER_MODEL_SHA256" &&
    verify_file "$MODEL_DIR/$PIPER_VOICE_ID.onnx.json" 4878 "$PIPER_CONFIG_SHA256" &&
    verify_file "$MODEL_DIR/MODEL_CARD" 308 "$PIPER_MODEL_CARD_SHA256"
}

case "$action" in
  prepare)
    install -d -m 0755 -o root -g root "$MODEL_DIR"
    fetch_file "$PIPER_VOICE_ID.onnx" 63201294 "$PIPER_MODEL_SHA256"
    fetch_file "$PIPER_VOICE_ID.onnx.json" 4878 "$PIPER_CONFIG_SHA256"
    fetch_file MODEL_CARD 308 "$PIPER_MODEL_CARD_SHA256"
    verify_all
    "${compose[@]}" pull piper
    "${compose[@]}" build moss
    printf 'Prepared pinned MOSS-TTS-Nano and Danish Piper fallback.\n'
    ;;
  up)
    verify_all || { printf 'Run prepare first.\n' >&2; exit 1; }
    "${compose[@]}" up -d --wait --wait-timeout 600
    ;;
  down)
    "${compose[@]}" down
    ;;
  status)
    "${compose[@]}" ps
    ;;
esac
